// The only script: progress polling and local price expiry. Pages work without it.
"use strict";

const SHANGHAI_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

// Shanghai calendar date as YYYY-MM-DD, independent of the device time zone.
function shanghaiToday() {
  const parts = SHANGHAI_FORMATTER.formatToParts(new Date());
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

// A price is valid through its expiry date; from the next Shanghai day hide every level.
// Returns true while some block with an expiry date has not expired yet.
function hideExpired() {
  const today = shanghaiToday();
  let pending = false;
  for (const block of document.querySelectorAll("[data-expires]")) {
    if (!block.dataset.expires) continue;
    if (today > block.dataset.expires) {
      for (const level of block.querySelectorAll("[data-levels]")) level.hidden = true;
      for (const note of block.querySelectorAll("[data-expired]")) note.hidden = false;
    } else {
      pending = true;
    }
  }
  return pending;
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

// A confirmation covers the scope that was on screen; editing the codes withdraws it.
function guardConsent(form) {
  const consent = form.querySelector("input[name=confirmed]");
  const codes = form.querySelector("[name=codes]");
  if (consent && codes) {
    codes.addEventListener("input", () => {
      consent.checked = false;
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const pending = hideExpired();
  for (const form of document.querySelectorAll("form.request")) guardConsent(form);
  if (pending) {
    const expiryTimer = window.setInterval(() => {
      if (!hideExpired()) window.clearInterval(expiryTimer);
    }, 60000);
  }
  for (const region of document.querySelectorAll("[data-poll]")) poll(region);
});
