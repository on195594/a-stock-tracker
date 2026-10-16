// The only script: progress polling, local price expiry, report jumps and the sticky
// mini-head. Pages work without it.
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

// A report target (cited fact, section, fact list) may sit in or be a collapsed
// <details>: open it, then scroll to the target.
function revealTarget(hash) {
  if (hash.length < 2) return;
  let id;
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return;
  }
  const target = document.getElementById(id);
  if (!target || !target.closest("[data-report]")) return;
  const details = target.closest("details");
  if (details) details.open = true;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

// Navigate to the fragment so :target applies, but undo the browser's instant jump
// and scroll smoothly from where the reader was.
function followAnchor(link) {
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    event.preventDefault();
    const x = window.scrollX;
    const y = window.scrollY;
    if (window.location.hash !== link.hash) {
      window.location.hash = link.hash;
      window.scrollTo(x, y);
    }
    revealTarget(link.hash);
  });
}

window.addEventListener("hashchange", () => revealTarget(window.location.hash));

// Show the report's mini-head while its head is above the viewport and the report
// itself still fills the top of the screen.
function stickReportHead(report) {
  const head = report.querySelector("[data-report-head]");
  const mini = report.querySelector("[data-mini-head]");
  if (!head || !mini || !("IntersectionObserver" in window)) return;
  let headAbove = false;
  let reportAtTop = false;
  const update = () => {
    mini.hidden = !(headAbove && reportAtTop);
  };
  new IntersectionObserver((entries) => {
    const entry = entries[entries.length - 1];
    headAbove = !entry.isIntersecting && entry.boundingClientRect.top < 0;
    update();
  }).observe(head);
  // Inset by the mini-head height so it never sits over the next block.
  new IntersectionObserver(
    (entries) => {
      reportAtTop = entries[entries.length - 1].isIntersecting;
      update();
    },
    { rootMargin: "-48px 0px 0px 0px" },
  ).observe(report);
}

document.addEventListener("DOMContentLoaded", () => {
  const pending = hideExpired();
  for (const form of document.querySelectorAll("form.request")) guardConsent(form);
  const anchors = document.querySelectorAll("a.citation, .jump-bar a, .mini-head a");
  for (const link of anchors) followAnchor(link);
  for (const report of document.querySelectorAll("[data-report]")) stickReportHead(report);
  revealTarget(window.location.hash);
  if (pending) {
    const expiryTimer = window.setInterval(() => {
      if (!hideExpired()) window.clearInterval(expiryTimer);
    }, 60000);
  }
  for (const region of document.querySelectorAll("[data-poll]")) poll(region);
});
