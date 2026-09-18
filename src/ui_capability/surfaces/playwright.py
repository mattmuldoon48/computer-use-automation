"""Private async Chromium adapter for the reviewed Member Operations UI.

All scripts below are fixed DOM-only helpers. Runtime callers receive typed
observations and actions, never browser handles, selectors, or evaluate APIs.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    Dialog,
    Frame,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from ..contracts import (
    Binding,
    Click,
    ExecutableAction,
    FailureCode,
    Fill,
    LabelLocator,
    Navigate,
    Observation,
    ObservedTarget,
    Ownership,
    PublicLiteral,
    RoleLocator,
    Scalar,
    Select,
    TableLocator,
    TableValueLocator,
    TargetSpec,
    Wait,
)
from ..errors import RuntimeFault
from ..evidence import EvidenceSink
from ..transport import TransportGuard, TransportPolicy, member_ops_transport
from ..values import bind_target

_TIMEOUT = 5000
_MAX_TARGETS = 200
_MAX_TEXT = 1024
_DISCOVERY = Path(__file__).with_name("discovery_dom.js").read_text(encoding="utf-8")
_FRAME_NAMES = frozenset({"navigation", "workspace"})
_CONTROLS = frozenset({"button", "a", "input", "select", "textarea", "other", "document"})
_KINDS = frozenset({"click", "change", "navigation"})
# Never read cookies, storage, application globals, hidden fields, or DOM source.
_METADATA = """(elements, discovery) => elements.map(element => {
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute('type') || 'text').toLowerCase();
    const inferred = tag === 'a' && element.hasAttribute('href') ? 'link'
        : tag === 'button' || (tag === 'input' && ['submit','button'].includes(type)) ? 'button'
        : tag === 'input' || tag === 'textarea' ? 'textbox'
        : tag === 'select' ? 'combobox'
        : /^h[1-6]$/.test(tag) ? 'heading' : ['td','th'].includes(tag) ? 'cell' : '';
    const control = inferred === 'link' ? 'link' : inferred === 'button' ? 'button' : tag;
    const role = element.getAttribute('role') || inferred;
    const text = (discovery && tag === 'select'
        ? Array.from(element.options)
            .filter(option => !option.hidden && getComputedStyle(option).display !== 'none')
            .map(option => option.label).join('\\n')
        : element.innerText || '').trim();
    const value = ['input','select','textarea'].includes(tag)
        && !['password','hidden','file'].includes(type) ? element.value : '';
    return {role: discovery ? role.slice(0, 65) : role, control,
        text: discovery ? text.slice(0, 1025) : text,
        value: discovery ? value.slice(0, 1025) : value,
        forbidden: tag === 'input' && ['file','password','hidden'].includes(type)};
})"""
_VISIBLE = """() => {
    const visible = element => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none'
            && box.width > 0 && box.height > 0;
    };
    const labels = 'h1,h2,h3,h4,h5,h6,legend,th,label,[role=status],[role=alert]';
    const text = Array.from(document.querySelectorAll(labels))
        .filter(visible).map(element => (element.innerText || '').trim())
        .filter(value => value.length > 0 && value.length <= 256).slice(0, 200);
    const destinations = Array.from(document.querySelectorAll('a[href]'))
        .filter(visible).map(element => element.href).slice(0, 200);
    return {text, destinations};
}"""
_SHELL = """() => ({
    text: document.body.innerText.trim(),
    dynamic: document.querySelectorAll(
        'input,textarea,select,canvas,video,img,svg,object,embed'
    ).length,
    frames: document.querySelectorAll('iframe,frame').length
})"""
_CLICK_NAVIGATION = """element => {
    if (element.tagName === 'A' && element.hasAttribute('href')) {
        return {url: element.href, method: 'GET', frame: element.target || '_self'};
    }
    if (['BUTTON','INPUT'].includes(element.tagName) && element.type === 'submit' && element.form) {
        const form = element.form;
        return {
            url: element.hasAttribute('formaction') ? element.formAction : form.action,
            method: (element.getAttribute('formmethod') || form.method || 'get').toUpperCase(),
            frame: element.getAttribute('formtarget') || form.target || '_self'
        };
    }
    return null;
}"""


def _literal(value: object) -> str:
    if not isinstance(value, PublicLiteral) or type(value.value) is not str:
        raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
    return value.value


class PlaywrightSurface:
    def __init__(
        self,
        binding: Binding,
        targets: dict[str, TargetSpec],
        inputs: dict[str, Scalar],
        evidence: EvidenceSink,
        transport: TransportPolicy,
        *,
        discovery: bool = False,
    ) -> None:
        self.__binding = binding
        self.__discovery = discovery
        self.__context_id = uuid4().hex
        self.__evidence = evidence
        self.__transport = transport
        self.__guard = TransportGuard(transport, evidence)
        # Aliases of one durable spec do not create false duplicate observations.
        unique: dict[str, TargetSpec] = {}
        for name in sorted(targets):
            spec = bind_target(targets[name], inputs, {})
            if spec.frame.kind == "named_frame" and spec.frame.name not in _FRAME_NAMES:
                raise RuntimeFault(FailureCode.INCOMPATIBLE)
            unique.setdefault(spec.model_dump_json(), spec)
        self.__targets = tuple(unique.values())
        self.__ownership = Ownership.AUTOMATION
        self.__playwright: Playwright | None = None
        self.__browser: Browser | None = None
        self.__context: BrowserContext | None = None
        self.__page: Page | None = None
        self.__dialog: Dialog | None = None
        self.__dialog_probe: asyncio.Task[Any] | None = None
        self.__last: Observation | None = None
        self.__refs: dict[str, ObservedTarget] = {}
        self.__closed = False

    @classmethod
    async def launch(
        cls,
        binding: Binding,
        targets: dict[str, TargetSpec],
        inputs: dict[str, Scalar],
        evidence: EvidenceSink,
        *,
        headed: bool = False,
        transport: TransportPolicy | None = None,
        discovery: bool = False,
    ) -> PlaywrightSurface:
        selected = member_ops_transport(binding) if transport is None else transport
        if selected.origin != binding.origin or not selected.permits(
            "GET", binding.origin + binding.entry_route
        ):
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        surface = cls(binding, targets, inputs, evidence, selected, discovery=discovery)
        try:
            surface.__playwright = await async_playwright().start()
            surface.__browser = await surface.__playwright.chromium.launch(headless=not headed)
            surface.__context = await surface.__guard.new_context(surface.__browser)
            surface.__context.set_default_timeout(_TIMEOUT)
            surface.__context.set_default_navigation_timeout(_TIMEOUT)
            await surface.__context.expose_binding("__uiCapabilityActivity", surface._activity)
            await surface.__context.add_init_script(
                script=Path(__file__).with_name("operator_capture.js").read_text(encoding="utf-8")
            )
            surface.__context.on("page", surface._attach_page)
            surface.__page = await surface.__context.new_page()
            await surface.__page.goto(
                binding.origin + binding.entry_route, wait_until="domcontentloaded"
            )
            await (
                surface.__page.frame_locator('iframe[name="workspace"]')
                .get_by_role("heading", name="Operations home", exact=True)
                .wait_for(state="visible", timeout=_TIMEOUT)
            )
            surface._frames()
            return surface
        except BaseException:
            await surface.close()
            raise RuntimeFault(FailureCode.INTERRUPTED) from None

    @property
    def binding(self) -> Binding:
        return self.__binding

    @property
    def context_id(self) -> str:
        return self.__context_id

    @property
    def features(self) -> frozenset[str]:
        return frozenset({"web", "frames", "visible_text", "form_controls"})

    @property
    def transport_denied_count(self) -> int:
        return self.__guard.denied_count

    def set_ownership(self, ownership: Ownership) -> None:
        if type(ownership) is not Ownership:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        # Synchronous by design: no queued browser command can lag an epoch change.
        self.__ownership = ownership

    def _attach_page(self, page: Page) -> None:
        page.on("dialog", self._hold_dialog)
        page.on("download", lambda download: download.cancel())

    def _hold_dialog(self, dialog: Dialog) -> None:
        # No message/prompt contents are read or persisted. Never auto-accept.
        self.__dialog = dialog

    def _activity(self, source: dict[str, Any], payload: object) -> None:
        if self.__ownership is not Ownership.HUMAN or type(payload) is not dict:
            return
        if set(payload) != {"kind", "control", "frame"}:
            return
        kind, control, claimed_frame = payload["kind"], payload["control"], payload["frame"]
        if not all(type(value) is str for value in (kind, control, claimed_frame)):
            return
        if kind not in _KINDS or control not in _CONTROLS:
            return
        frame = source.get("frame")
        if not isinstance(frame, Frame) or source.get("page") is not self.__page:
            return
        if self.__transport.path_for(frame.url) is None:
            return
        actual_frame = "main" if frame is self._page().main_frame else frame.name
        if actual_frame not in {"main", "navigation", "workspace"} or actual_frame != claimed_frame:
            return
        if actual_frame != "main" and frame.parent_frame is not self._page().main_frame:
            return
        self.__evidence.human_activity(kind, control, actual_frame)

    def _page(self) -> Page:
        if self.__page is None or self.__closed:
            raise RuntimeFault(FailureCode.INTERRUPTED)
        return self.__page

    def _frames(self) -> dict[str, Frame]:
        page = self._page()
        context = self.__context
        if context is None or len(context.pages) != 1 or context.pages[0] is not page:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        if self.__transport.path_for(page.url) != self.__binding.entry_route:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        frames = {"main": page.main_frame}
        for frame in page.frames:
            path = self.__transport.path_for(frame.url)
            if path is None or not any(rule.path == path for rule in self.__transport.rules):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if frame is page.main_frame:
                continue
            if (
                frame.name not in _FRAME_NAMES
                or frame.name in frames
                or frame.parent_frame is not page.main_frame
                or (frame.name == "navigation" and path != "/navigation")
                or (frame.name == "workspace" and not path.startswith("/workspace/"))
            ):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            frames[frame.name] = frame
        if set(frames) != {"main", "navigation", "workspace"}:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        return frames

    @property
    def browser_version(self) -> str:
        if self.__browser is None:
            raise RuntimeFault(FailureCode.INTERRUPTED)
        return self.__browser.version

    async def _dialog_active(self) -> bool:
        if self.__dialog is None:
            return False
        # Retain one pending read rather than cancelling Playwright's protocol
        # waiter repeatedly. It settles when the operator dismisses the dialog
        # or the context closes, and close() always consumes its outcome.
        if self.__dialog_probe is None:
            self.__dialog_probe = asyncio.create_task(self._page().evaluate("1"))
        done, _ = await asyncio.wait({self.__dialog_probe}, timeout=0.25)
        if not done:
            return True
        self.__dialog_probe.result()
        self.__dialog_probe = None
        self.__dialog = None
        return False

    async def _locator(self, spec: TargetSpec, frames: dict[str, Frame]) -> Locator:
        frame = frames["main" if spec.frame.kind == "main" else cast(str, spec.frame.name)]
        root: Frame | Locator = frame
        if spec.section is not None:
            section = _literal(spec.section)
            groups = frame.get_by_role("group", name=section, exact=True)
            sections = frame.locator("section").filter(
                has=frame.get_by_role("heading", name=section, exact=True)
            )
            root = groups.or_(sections).filter(visible=True)
            if await root.count() > 1:
                raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
        locator = spec.locator
        if isinstance(locator, RoleLocator):
            resolved = root.get_by_role(locator.role, name=_literal(locator.name), exact=True)
        elif isinstance(locator, LabelLocator):
            resolved = root.get_by_label(_literal(locator.label), exact=True)
        elif isinstance(locator, (TableLocator, TableValueLocator)):
            caption = _literal(locator.label)
            # Caption and sibling relation are the contract, not an ordinal DOM
            # path or generated id. Exact visible captions are never weakened.
            captions = root.locator("tr > th, tr > td").filter(
                has_text=re.compile(r"^\s*" + re.escape(caption) + r"\s*$"), visible=True
            )
            if await captions.count() > 1:
                raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
            cell = captions.locator("xpath=following-sibling::td[count(../td)=1]")
            resolved = (
                cell if isinstance(locator, TableValueLocator) else cell.locator(locator.control)
            )
        else:
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        return resolved.filter(visible=True)

    async def _metadata(
        self, locator: Locator, spec: TargetSpec, *, derived: bool = False
    ) -> list[ObservedTarget]:
        metadata = await locator.evaluate_all(_METADATA, self.__discovery)
        if not isinstance(metadata, list) or len(metadata) > _MAX_TARGETS:
            raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
        grounding: Literal["visible_role", "visible_label", "visible_table_caption"]
        grounding = (
            "visible_role"
            if isinstance(spec.locator, RoleLocator)
            else (
                "visible_label"
                if isinstance(spec.locator, LabelLocator)
                else "visible_table_caption"
            )
        )
        targets = []
        for item in metadata:
            if item["forbidden"]:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if self.__discovery and (
                len(item["text"]) > _MAX_TEXT
                or len(item["value"]) > _MAX_TEXT
                or len(item["role"]) > 64
            ):
                raise RuntimeFault(FailureCode.INCOMPATIBLE)
            targets.append(
                ObservedTarget(
                    ref="t_" + uuid4().hex,
                    spec=spec,
                    role=item["role"],
                    control=item["control"],
                    visible=True,
                    text=item["text"],
                    value=item["value"],
                    grounding=grounding,
                    derived=derived,
                )
            )
        return targets

    async def _derived_targets(self, frames: dict[str, Frame]) -> dict[str, ObservedTarget]:
        targets: dict[str, ObservedTarget] = {}
        for name in sorted(frames):
            frame = frames[name]
            candidates = await frame.evaluate_handle(_DISCOVERY)
            try:
                properties = await candidates.get_properties()
                for candidate in properties.values():
                    element = await candidate.get_property("element")
                    specs = await candidate.get_property("specs")
                    try:
                        raw_specs = await specs.json_value()
                        for raw in raw_specs:
                            spec = TargetSpec.model_validate(
                                {
                                    **raw,
                                    "frame": (
                                        {"kind": "main"}
                                        if name == "main"
                                        else {"kind": "named_frame", "name": name}
                                    ),
                                }
                            )
                            key = spec.model_dump_json()
                            if key in targets:
                                continue
                            try:
                                locator = await self._locator(spec, frames)
                            except RuntimeFault as fault:
                                if fault.code == FailureCode.AMBIGUOUS_TARGET:
                                    continue
                                raise
                            if await locator.count() != 1:
                                continue
                            # A name match alone is insufficient: the durable spec
                            # must point back to this exact enumerated DOM node.
                            if not await locator.evaluate(
                                "(resolved, candidate) => resolved === candidate", element
                            ):
                                continue
                            observed = await self._metadata(locator, spec, derived=True)
                            if len(observed) != 1:
                                continue
                            targets[key] = observed[0]
                            if len(targets) > _MAX_TARGETS:
                                raise RuntimeFault(FailureCode.INCOMPATIBLE)
                    finally:
                        await element.dispose()
                        await specs.dispose()
                        await candidate.dispose()
            finally:
                await candidates.dispose()
        return targets

    async def observe(self, run_id: str, session_id: str, epoch: int) -> Observation:
        try:
            frames = self._frames()
            route = self.__transport.path_for(frames["workspace"].url)
            if route is None:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if await self._dialog_active():
                observed = Observation(
                    observation_id=uuid4().hex,
                    run_id=run_id,
                    session_id=session_id,
                    ownership_epoch=epoch,
                    origin=self.binding.origin,
                    route=route,
                    targets=() if self.__last is None else self.__last.targets,
                    visible_text=() if self.__last is None else self.__last.visible_text,
                    dialog="unknown",
                )
                self.__refs = {}
                return observed
            derived = await self._derived_targets(frames) if self.__discovery else {}
            targets: list[ObservedTarget] = list(derived.values())
            for spec in self.__targets:
                if spec.model_dump_json() not in derived:
                    targets.extend(await self._metadata(await self._locator(spec, frames), spec))
            if self.__discovery:
                if len(targets) > _MAX_TARGETS:
                    raise RuntimeFault(FailureCode.INCOMPATIBLE)
                targets.sort(key=lambda target: target.spec.model_dump_json())
            text: list[str] = []
            destinations: set[str] = set()
            for frame in frames.values():
                visible = await frame.evaluate(_VISIBLE)
                text.extend(visible["text"])
                for destination in visible["destinations"]:
                    path = self.__transport.path_for(destination)
                    if path is not None and self.__transport.permits("GET", destination):
                        destinations.add(path)
            if self.__discovery and (len(text) > _MAX_TARGETS or len(destinations) > _MAX_TARGETS):
                raise RuntimeFault(FailureCode.INCOMPATIBLE)
            self._frames()
            observed = Observation(
                observation_id=uuid4().hex,
                run_id=run_id,
                session_id=session_id,
                ownership_epoch=epoch,
                origin=self.binding.origin,
                route=route,
                targets=tuple(targets),
                visible_text=tuple(text),
                destinations=tuple(sorted(destinations)),
            )
            self.__last = observed
            self.__refs = {target.ref: target for target in targets}
            return observed
        except RuntimeFault:
            raise
        except Exception:
            raise RuntimeFault(FailureCode.INTERRUPTED) from None

    async def _settle(self) -> None:
        # Called only after a known navigation commits, not on the old document.
        # Replay owns bounded checkpoint retries after DOMContentLoaded.
        if self.__dialog is not None:
            return
        for frame in self._frames().values():
            await frame.wait_for_load_state("domcontentloaded", timeout=_TIMEOUT)

    async def perform(
        self, action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
    ) -> None:
        if self.__ownership is not Ownership.AUTOMATION:
            raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
        try:
            frames = self._frames()
            if await self._dialog_active():
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if isinstance(action, Wait):
                await asyncio.sleep(action.milliseconds / 1000)
                return
            if isinstance(action, Navigate):
                if type(value) is not str or not self.__transport.permits(
                    "GET", self.binding.origin + value
                ):
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
                await frames["workspace"].goto(
                    self.binding.origin + value, wait_until="domcontentloaded"
                )
                await self._settle()
                return
            if not isinstance(action, (Click, Fill, Select)) or target is None:
                raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
            if self.__refs.get(target.ref) != target or (
                target.spec not in self.__targets and not (self.__discovery and target.derived)
            ):
                raise RuntimeFault(FailureCode.STALE_OBSERVATION)
            locator = await self._locator(target.spec, frames)
            current = await self._metadata(locator, target.spec, derived=target.derived)
            if len(current) != 1:
                raise RuntimeFault(
                    FailureCode.AMBIGUOUS_TARGET if current else FailureCode.TARGET_NOT_FOUND
                )
            before = target.model_dump(exclude={"ref"})
            if current[0].model_dump(exclude={"ref"}) != before:
                raise RuntimeFault(FailureCode.STALE_OBSERVATION)
            if isinstance(action, Fill):
                if type(value) is not str or target.control not in {"input", "textarea"}:
                    raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
                await locator.fill(value, timeout=_TIMEOUT)
            elif isinstance(action, Select):
                if type(value) is not str or target.control != "select":
                    raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
                await locator.select_option(value=value, timeout=_TIMEOUT)
            else:
                if target.control not in {"button", "link"}:
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
                if await locator.get_attribute("download") is not None:
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
                navigation = await locator.evaluate(_CLICK_NAVIGATION)
                if navigation is None:
                    await locator.click(timeout=_TIMEOUT)
                else:
                    if not self.__transport.permits(navigation["method"], navigation["url"]):
                        raise RuntimeFault(FailureCode.POLICY_DENIED)
                    frame_name = navigation["frame"]
                    if frame_name == "_self":
                        frame_name = (
                            "main" if target.spec.frame.kind == "main" else target.spec.frame.name
                        )
                    if frame_name not in frames:
                        raise RuntimeFault(FailureCode.POLICY_DENIED)
                    destination_frame = frames[frame_name]
                    # Arm before dispatch; URL comparison would miss same-URL
                    # POST forms and waiting on old load state races iframe links.
                    async with self._page().expect_event(
                        "framenavigated",
                        predicate=lambda moved: moved is destination_frame,
                        timeout=_TIMEOUT,
                    ):
                        await locator.click(no_wait_after=True, timeout=_TIMEOUT)
                await self._settle()
            self.__refs = {}
        except RuntimeFault:
            raise
        except Exception:
            if self.__dialog is not None:
                return  # Observation reports held dialog; do not auto-dismiss.
            raise RuntimeFault(FailureCode.UNKNOWN_ACTION_OUTCOME) from None

    async def capture_safe(self, path: Path) -> dict[str, object]:
        """Persist only a pre-masked PNG after validating the reviewed shell.

        Workspace contents are entirely opaque, including headings, form fields,
        restricted link names, records and messages. Static outer chrome remains.
        """
        try:
            frames = self._frames()
            if await self._dialog_active():
                return {"withheld": True, "reason": "dialog_open"}
            shell = await frames["main"].evaluate(_SHELL)
            navigation = await frames["navigation"].evaluate(_SHELL)
            shell["text"] = "\n".join(
                line.strip() for line in shell["text"].splitlines() if line.strip()
            )
            navigation["text"] = "\n".join(
                line.strip() for line in navigation["text"].splitlines() if line.strip()
            )
            if (
                shell["text"].splitlines()
                != [
                    "Member Operations Sandbox",
                    "Training environment · Synthetic records only",
                    "Hand-authored test fixture · Not connected to a financial institution",
                ]
                or shell["dynamic"] != 0
                or shell["frames"] != 2
                or navigation["text"].splitlines()
                != [
                    "OPERATIONS MENU",
                    "Member search",
                    "Member services",
                    "Training desk",
                ]
                or navigation["dynamic"] != 0
                or navigation["frames"] != 0
            ):
                return {"withheld": True, "reason": "unreviewed_shell"}
            mask = self._page().locator('iframe[name="workspace"]')
            if await mask.count() != 1:
                return {"withheld": True, "reason": "unsafe_structure"}
            rectangle = await mask.bounding_box()
            if rectangle is None:
                return {"withheld": True, "reason": "unsafe_structure"}
            # mask is applied by Chromium before bytes exist; no unmasked image
            # or raw trace is ever produced, even temporarily on disk.
            image = await self._page().screenshot(
                type="png",
                full_page=True,
                mask=[mask],
                mask_color="#202020",
                animations="disabled",
                caret="hide",
                scale="css",
                timeout=_TIMEOUT,
            )
            self._frames()
            if self.__dialog is not None:
                return {"withheld": True, "reason": "dialog_open"}
            await asyncio.to_thread(path.write_bytes, image)
            return {
                "withheld": False,
                "reason": "workspace_masked",
                "frame_count": len(frames),
                "target_count": 0 if self.__last is None else len(self.__last.targets),
                "mask_rects": [dict(rectangle)],
            }
        except Exception:
            return {"withheld": True, "reason": "unsafe_capture"}

    async def close(self) -> None:
        if self.__closed:
            return
        self.__closed = True
        try:
            if self.__context is not None:
                await self.__context.close()
        finally:
            await self.__guard.close()
            try:
                if self.__browser is not None:
                    await self.__browser.close()
            finally:
                if self.__playwright is not None:
                    await self.__playwright.stop()
                if self.__dialog_probe is not None:
                    await asyncio.gather(self.__dialog_probe, return_exceptions=True)
                    self.__dialog_probe = None
