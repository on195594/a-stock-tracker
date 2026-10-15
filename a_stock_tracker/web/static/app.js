// The only script: progress polling and local price expiry. Pages work without it.
"use strict";

// Shanghai calendar date as YYYY-MM-DD, independent of the device time zone.
function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

// A price is valid through its expiry date; from the next Shanghai day hide every level.
function hideExpired() {
  const today = shanghaiToday();
  for (const block of document.querySelectorAll("[data-expires]")) {
    if (block.dataset.expires && today > block.dataset.expires) {
      for (const level of block.querySelectorAll("[data-levels]")) level.hidden = true;
      for (const note of block.querySelectorAll("[data-expired]")) note.hidden = false;
    }
  }
}

function poll(region) {
  const initial = region.dataset.status;
  let failures = 0;
  const stop = (message) => {
    region.replaceChildren();
    const text = document.createElement("p");
    text.textContent = message;
    region.append(text);
  };
  const tick = async () => {
    let response;
    try {
      response = await fetch(region.dataset.poll, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
    } catch {
      response = null;
    }
    if (response && (response.status === 401 || response.status === 403)) {
      // Revoked or expired: stop and clear progress instead of revealing late results.
      stop("登录已失效，请重新登录后查看进度。");
      return;
    }
    if (response && response.ok) {
      failures = 0;
      const body = await response.json();
      if (body.status && body.status !== initial) {
        window.location.reload();
        return;
      }
    } else if (++failures >= 3) {
      stop("研究状态暂不可确认；任务可能仍在后台执行。刷新页面重新查看原请求，不另起一轮收费。");
      return;
    }
    window.setTimeout(tick, 3000);
  };
  window.setTimeout(tick, 3000);
}

document.addEventListener("DOMContentLoaded", () => {
  hideExpired();
  window.setInterval(hideExpired, 60000);
  for (const region of document.querySelectorAll("[data-poll]")) poll(region);
});
