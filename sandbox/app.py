"""Server-rendered synthetic UI; the in-process oracle is only for the test harness."""

import asyncio
import re
import secrets
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sandbox.fixtures import MEMBERS, PRODUCTS, SCENARIOS, Member, Product

_COOKIE = "member_ops_session"


@dataclass(frozen=True)
class Review:
    member_id: str
    product_code: str
    nickname: str


@dataclass
class DemoSession:
    member_id: str | None = None
    detail_seen: bool = False
    prepared: bool = False
    review: Review | None = None
    expired: bool = False
    expired_once: bool = False
    notice_dismissed: bool = False


@dataclass
class Oracle:
    """Harness-owned observations, never exposed by HTTP or browser scripts."""

    ledger: list[dict[str, str | int | bool]] = field(default_factory=list)
    submit_requests: int = 0
    request_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    signin_count: int = 0


def create_app(scenario: str = "happy") -> FastAPI:
    """Create an isolated application using a hand-authored fault fixture."""
    if scenario not in SCENARIOS:
        raise ValueError("Unknown sandbox scenario")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    oracle = Oracle()
    app.state.oracle = oracle
    sessions: dict[str, DemoSession] = {}
    templates = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(("html",)),
    )

    @app.middleware("http")
    async def session_scope(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        oracle.request_counts[(request.method, request.url.path)] += 1
        token = request.cookies.get(_COOKIE)
        fresh = token not in sessions
        if fresh:
            token = secrets.token_urlsafe(32)
            sessions[token] = DemoSession()
        assert token is not None
        request.state.session_token = token
        request.state.session = sessions[token]
        response = await call_next(request)
        if fresh or request.state.session_token != token:
            response.set_cookie(
                _COOKIE,
                request.state.session_token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/",
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def render(template: str, **context: object) -> HTMLResponse:
        return HTMLResponse(
            templates.get_template(template).render(
                render_id=f"r{secrets.token_hex(6)}", **context
            ),
        )

    def workspace(heading: str, **context: object) -> HTMLResponse:
        return render("workspace.html", heading=heading, **context)

    def member_for(session: DemoSession) -> Member | None:
        return MEMBERS.get(session.member_id or "")

    def expired_view() -> HTMLResponse:
        return workspace("Session expired", view="signin")

    def unavailable_session(session: DemoSession) -> HTMLResponse | None:
        if session.expired:
            return expired_view()
        if member_for(session) is None or not session.detail_seen:
            return workspace(
                "Business validation rejected",
                message="Select a member before preparing an account.",
            )
        if scenario == "permission_denied":
            return workspace(
                "Permission denied", message="This demo operator cannot prepare sub-accounts."
            )
        return None

    def prepare_view(session: DemoSession) -> HTMLResponse:
        session.prepared = True
        session.review = None
        return workspace(
            "Prepare sub-account",
            view="prepare",
            member=member_for(session),
            products=PRODUCTS.values(),
            duplicate_review=scenario == "duplicate_target",
        )

    def product_allowed(session: DemoSession, product: Product) -> bool:
        member = member_for(session)
        return (
            member is not None
            and product.code in member.eligible_products
            and scenario != "product_ineligible"
        )

    @app.get("/", response_class=HTMLResponse)
    async def shell() -> HTMLResponse:
        return render("shell.html")

    @app.get("/navigation", response_class=HTMLResponse)
    async def navigation() -> HTMLResponse:
        return render("navigation.html")

    @app.get("/workspace/home", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return workspace("Operations home", view="home")

    @app.get("/workspace/search", response_class=HTMLResponse)
    async def search_form() -> HTMLResponse:
        return workspace("Member search", view="search")

    @app.post("/workspace/search", response_class=HTMLResponse)
    async def search(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        if session.expired:
            return expired_view()
        form = await request.form()
        member_id = form.get("member_id")
        session.member_id = None
        session.detail_seen = False
        session.prepared = False
        session.review = None
        session.notice_dismissed = False
        if not isinstance(member_id, str) or re.fullmatch(r"[0-9]{6}", member_id) is None:
            return workspace(
                "Business validation rejected", message="Enter a six-digit member number."
            )
        if scenario == "slow_load":
            await asyncio.sleep(0.4)
        member = MEMBERS.get(member_id)
        if member is None:
            return workspace("Member not found", message="No matching synthetic member was found.")
        session.member_id = member.member_id
        return workspace("Search results", view="results", member=member)

    @app.get("/workspace/member", response_class=HTMLResponse)
    async def details(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        if session.expired:
            return expired_view()
        member = member_for(session)
        if member is None:
            return workspace("Member not found", message="Search for a synthetic member first.")
        session.detail_seen = True
        return workspace("Member details", view="details", member=member)

    @app.get("/workspace/prepare", response_class=HTMLResponse)
    async def prepare(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        blocked = unavailable_session(session)
        if blocked is not None:
            return blocked
        if scenario == "session_expired" and not session.expired_once:
            session.expired = True
            session.expired_once = True
            session.prepared = False
            session.review = None
            return expired_view()
        if scenario == "known_interstitial" and not session.notice_dismissed:
            return workspace("Service notice", view="notice", member=member_for(session))
        if scenario == "unknown_dialog":
            return workspace("Unexpected confirmation", view="dialog", member=member_for(session))
        return prepare_view(session)

    @app.post("/workspace/signin", response_class=HTMLResponse)
    async def signin(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        form = await request.form()
        operator = form.get("demo_operator")
        if (
            not session.expired
            or not session.detail_seen
            or member_for(session) is None
            or not isinstance(operator, str)
            or not operator.strip()
            or len(operator) > 32
        ):
            return workspace(
                "Business validation rejected", message="A pending demo sign-in is required."
            )
        old_token: str = request.state.session_token
        new_token = secrets.token_urlsafe(32)
        sessions[new_token] = session
        del sessions[old_token]
        request.state.session_token = new_token
        session.expired = False
        oracle.signin_count += 1
        return prepare_view(session)

    @app.post("/workspace/dismiss", response_class=HTMLResponse)
    async def dismiss(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        blocked = unavailable_session(session)
        if blocked is not None:
            return blocked
        if scenario != "known_interstitial" or session.notice_dismissed:
            return workspace("Business validation rejected", message="There is no pending notice.")
        session.notice_dismissed = True
        return prepare_view(session)

    @app.post("/workspace/review", response_class=HTMLResponse)
    async def review(request: Request) -> HTMLResponse:
        session: DemoSession = request.state.session
        blocked = unavailable_session(session)
        if blocked is not None:
            return blocked
        session.review = None
        if not session.prepared:
            return workspace(
                "Business validation rejected", message="Open account preparation first."
            )
        form = await request.form()
        code = form.get("product_code")
        nickname = form.get("nickname")
        product = PRODUCTS.get(code) if isinstance(code, str) else None
        if product is None or not product_allowed(session, product):
            return workspace(
                "Product unavailable", message="This product is unavailable for the member."
            )
        if (
            not isinstance(nickname, str)
            or not nickname.strip()
            or len(nickname) > 32
            or any(ord(character) < 32 for character in nickname)
            or scenario == "validation_rejected"
        ):
            return workspace(
                "Business validation rejected", message="The requested nickname cannot be used."
            )
        assert session.member_id is not None
        session.review = Review(session.member_id, product.code, nickname)
        display_member = member_for(session)
        if scenario == "wrong_member_review":
            display_member = MEMBERS["000099" if session.member_id != "000099" else "000042"]
        return workspace(
            "Review sub-account",
            view="review",
            member=display_member,
            product=product,
            nickname=nickname,
            submitted="false",
        )

    @app.post("/workspace/submit", response_class=HTMLResponse)
    async def submit(request: Request) -> HTMLResponse:
        oracle.submit_requests += 1
        session: DemoSession = request.state.session
        blocked = unavailable_session(session)
        if blocked is not None:
            return blocked
        pending = session.review
        if pending is None or pending.member_id != session.member_id:
            return workspace(
                "Business validation rejected", message="A current review is required."
            )
        product = PRODUCTS[pending.product_code]
        if not product_allowed(session, product):
            return workspace(
                "Product unavailable", message="This product is unavailable for the member."
            )
        oracle.ledger.append(
            {
                "member_id": pending.member_id,
                "product_code": pending.product_code,
                "nickname": pending.nickname,
                "monthly_fee_minor": product.monthly_fee_minor,
                "currency": product.currency,
                "submitted": True,
            }
        )
        session.review = None
        session.prepared = False
        return workspace(
            "Review sub-account",
            view="review",
            member=member_for(session),
            product=product,
            nickname=pending.nickname,
            submitted="true",
        )

    return app
