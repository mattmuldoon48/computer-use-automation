// Fixed, data-minimizing telemetry only. Backend ownership grants no authority
// to this script; synthetic events are not proof of an actual human operator.
(() => {
  const frame = window === window.top ? "main"
    : window.name === "navigation" ? "navigation"
    : window.name === "workspace" ? "workspace" : null;
  if (frame === null) return;
  const send = (kind, control) => {
    const recorder = window.__uiCapabilityActivity;
    if (typeof recorder === "function") {
      void recorder({kind, control, frame}).catch(() => {});
    }
  };
  const controlFor = (target) => {
    if (!(target instanceof Element)) return "other";
    const control = target.closest("button,a,input,select,textarea,form");
    if (!control) return "other";
    const tag = control.tagName.toLowerCase();
    return tag === "form" ? "other" : tag;
  };
  document.addEventListener("click", event => send("click", controlFor(event.target)), true);
  document.addEventListener("change", event => send("change", controlFor(event.target)), true);
  window.addEventListener("pageshow", () => send("navigation", "document"));
  window.addEventListener("popstate", () => send("navigation", "document"));
})();
