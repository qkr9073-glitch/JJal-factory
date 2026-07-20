const els = {
  accountInput: document.getElementById("accountInput"),
  openBtn: document.getElementById("openBtn"),
  startBtn: document.getElementById("startBtn"),
  stopBtn: document.getElementById("stopBtn"),
  clearBtn: document.getElementById("clearBtn"),
  minViews: document.getElementById("minViews"),
  csvBtn: document.getElementById("csvBtn"),
  jsonBtn: document.getElementById("jsonBtn"),
  copyBtn: document.getElementById("copyBtn"),
  sendLocalBtn: document.getElementById("sendLocalBtn"),
  serverUrl: document.getElementById("serverUrl"),
  modeLabel: document.getElementById("modeLabel"),
  modeHint: document.getElementById("modeHint"),
  status: document.getElementById("status"),
  count: document.getElementById("count"),
  previewList: document.getElementById("previewList")
};

let items = [];
let pollTimer = null;

init();

const DEFAULT_SERVER = "https://jjal.traffic-charger.com";

function init() {
  bind();
  loadServerUrl();
  detectActiveAccount();
  refresh();
  pollTimer = setInterval(refresh, 1200);
}

function loadServerUrl() {
  try {
    chrome.storage.local.get(["serverUrl"], (r) => {
      if (els.serverUrl) els.serverUrl.value = (r && r.serverUrl) || DEFAULT_SERVER;
    });
  } catch {
    if (els.serverUrl) els.serverUrl.value = DEFAULT_SERVER;
  }
}

function serverUrl() {
  const v = (els.serverUrl && els.serverUrl.value || "").trim().replace(/\/+$/, "");
  return v || DEFAULT_SERVER;
}

function bind() {
  els.openBtn.addEventListener("click", openReelsTab);
  els.startBtn.addEventListener("click", startWatch);
  els.stopBtn.addEventListener("click", stopWatch);
  els.clearBtn.addEventListener("click", clearItems);
  els.minViews.addEventListener("change", render);
  els.csvBtn.addEventListener("click", saveCsv);
  els.jsonBtn.addEventListener("click", saveJson);
  els.copyBtn.addEventListener("click", copyUrls);
  els.sendLocalBtn.addEventListener("click", sendToLocalApp);
  if (els.serverUrl) els.serverUrl.addEventListener("change", () => {
    try { chrome.storage.local.set({ serverUrl: serverUrl() }); } catch {}
  });
}

async function detectActiveAccount() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const account = cleanAccountName(tab?.url || "");
    if (account && !els.accountInput.value.trim()) els.accountInput.value = account;
  } catch {
    // Optional convenience only.
  }
}

async function openReelsTab() {
  const account = els.accountInput.value.trim();
  if (!account) return setStatus("계정명/채널 URL을 입력하세요");
  const response = await send({ type: "OPEN_REELS_TAB", account });
  if (response.account && !els.accountInput.value.trim()) els.accountInput.value = response.account;
  setStatus(response.error || "소스 탭 열림");
}

async function startWatch() {
  const response = await send({ type: "START_WATCH" });
  if (response.account && !els.accountInput.value.trim()) els.accountInput.value = response.account;
  const base = response.scanned ? `감시 중 / 화면 ${response.scanned}개 스캔` : response.reloaded ? "감시 중 / 현재 탭 새로고침" : "감시 중";
  setStatus(response.error || (response.note ? `${base} · ${response.note}` : base));
  await refresh();
}

async function stopWatch() {
  const response = await send({ type: "STOP_WATCH" });
  setStatus(response.error || "중지됨");
  await refresh();
}

async function clearItems() {
  const response = await send({ type: "CLEAR_ITEMS" });
  setStatus(response.error || "초기화됨");
  await refresh();
}

async function refresh() {
  const response = await send({ type: "GET_STATE" });
  if (response.error) {
    setStatus(response.error);
    return;
  }

  if (response.account && !els.accountInput.value.trim()) els.accountInput.value = response.account;
  items = response.items || [];
  els.status.textContent = response.watching ? "감시 중" : "대기";
  renderMode(response.currentUrl || "");
  render();
}

function renderMode(url) {
  const mode = currentMode(url);
  els.modeLabel.textContent = mode.label;
  els.modeHint.textContent = mode.hint;
}

function render() {
  const filtered = filteredItems();
  els.count.textContent = getMinViews() ? `${filtered.length}/${items.length}개` : `${items.length}개`;

  els.previewList.innerHTML = "";
  for (const item of filtered.slice(0, 15)) {
    const li = document.createElement("li");
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.textContent = item.url;

    const meta = document.createElement("span");
    meta.className = "meta";
    const platform = item.platform === "youtube" ? "YouTube" : "Instagram";
    const title = item.title ? ` / ${item.title}` : "";
    meta.textContent = `${platform} / ${formatNumber(item.viewCount || 0)} views${title}`;

    li.append(link, meta);
    els.previewList.append(li);
  }
}

function saveCsv() {
  const filtered = filteredItems();
  if (!filtered.length) return setStatus("필터에 맞는 항목 없음");

  const header = ["rank", "platform", "viewCount", "likeCount", "commentCount", "url", "shortcode", "title", "channel", "takenAt"];
  const rows = filtered.map((item, index) => [
    index + 1,
    item.platform || "",
    item.viewCount || "",
    item.likeCount || "",
    item.commentCount || "",
    item.url || "",
    item.shortcode || "",
    item.title || "",
    item.channel || "",
    item.takenAt || ""
  ]);
  const text = [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
  downloadText(`kangaroo-shortform-${dateStamp()}.csv`, text, "text/csv;charset=utf-8");
  setStatus("CSV 저장됨");
}

function saveJson() {
  const filtered = filteredItems();
  if (!filtered.length) return setStatus("필터에 맞는 항목 없음");
  downloadText(`kangaroo-shortform-${dateStamp()}.json`, JSON.stringify(filtered, null, 2), "application/json;charset=utf-8");
  setStatus("JSON 저장됨");
}

async function copyUrls() {
  const filtered = filteredItems();
  if (!filtered.length) return setStatus("필터에 맞는 항목 없음");
  await navigator.clipboard.writeText(filtered.map((item) => item.url).join("\n"));
  setStatus("URL 복사됨");
}

async function sendToLocalApp() {
  const filtered = filteredItems();
  if (!filtered.length) return setStatus("필터에 맞는 항목 없음");

  const account = cleanAccountName(els.accountInput.value.trim()) || guessAccountFromItems(filtered) || "shortform";
  setStatus("짤공장으로 전송 중...");

  const response = await send({ type: "SEND_TO_LOCAL_APP", items: filtered, account, serverUrl: serverUrl() });
  if (!response || (response.error && !response.fallback)) return setStatus((response && response.error) || "응답 없음 — 확장 새로고침 후 재시도");
  if (response.fallback) return setStatus(response.error || "URL만 전달됨");

  const sent = response.count || filtered.length;
  setStatus(response.total ? `✅ 짤공장으로 보냄 ${sent}개 (창고 누적 ${response.total})` : `✅ 짤공장으로 보냄 ${sent}개`);
}

function currentMode(url) {
  if (/instagram\.com/i.test(url)) {
    return {
      label: "Instagram",
      hint: "릴스 탭에서 수집 시작을 누르면 새로고침됩니다. 이후 화면을 아래로 스크롤하세요."
    };
  }

  if (/youtube\.com/i.test(url)) {
    return {
      label: "YouTube",
      hint: "Shorts 탭(쇼츠) 또는 동영상 탭(롱폼)에서 수집하세요. 모두 watch 링크로 저장됩니다."
    };
  }

  return {
    label: "지원 안 됨",
    hint: "Instagram 릴스 탭 또는 YouTube Shorts 탭에서 사용하세요."
  };
}

function filteredItems() {
  const minViews = getMinViews();
  if (!minViews) return items;
  return items.filter((item) => (Number(item.viewCount) || 0) >= minViews);
}

function getMinViews() {
  return Number(els.minViews.value || 0);
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function setStatus(value) {
  els.status.textContent = value;
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function cleanAccountName(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const decode = (input) => { try { return decodeURIComponent(input); } catch { return input; } };

  try {
    const url = new URL(text.startsWith("http") ? text : `https://www.instagram.com/${text}/`);
    const host = url.hostname.replace(/^www\./, "").replace(/^m\./, "").toLowerCase();
    if (host.includes("youtube.com")) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts[0]?.startsWith("@")) return decode(parts[0].replace(/^@/, ""));
      if (parts[0] === "channel" && parts[1]) return decode(parts[1]);
      return "youtube_shorts";
    }
    const first = decode(url.pathname.split("/").filter(Boolean)[0]?.replace(/^@/, "") || "");
    if (["accounts", "explore", "p", "reel", "reels", "stories"].includes(first)) return "";
    return first;
  } catch {
    return decode(text.replace(/^@/, ""));
  }
}

function guessAccountFromItems(list) {
  const youtubeChannel = list.find((item) => item.platform === "youtube" && item.channel)?.channel;
  if (youtubeChannel) return youtubeChannel;
  if (list.some((item) => item.platform === "youtube")) return "youtube_shorts";
  if (list.some((item) => item.platform === "instagram")) return "instagram_reels";
  return "";
}

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function formatNumber(value) {
  return new Intl.NumberFormat("ko-KR").format(value || 0);
}

function dateStamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}
