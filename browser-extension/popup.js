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
  sendCookiesBtn: document.getElementById("sendCookiesBtn"),
  serverUrl: document.getElementById("serverUrl"),
  memberCode: document.getElementById("memberCode"),
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
  setTimeout(checkUpdate, 600);   // serverUrl/memberCode 로드 후 버전 확인
  detectActiveAccount();
  refresh();
  pollTimer = setInterval(refresh, 1200);
}

async function checkUpdate() {
  try {
    const r = await fetch(serverUrl() + "/ext.version");
    const j = await r.json();
    const mine = chrome.runtime.getManifest().version;
    if (j && j.version && j.version !== mine) {
      const b = document.getElementById("updateBanner");
      if (b) {
        const dl = serverUrl() + "/ext.zip?code=" + encodeURIComponent(memberCode() || "");
        b.style.display = "block";
        b.innerHTML = "🔄 새 버전 v" + j.version + " (현재 v" + mine + ") — "
          + '<a href="' + dl + '" target="_blank" style="color:#b7ffe0">zip 다운로드</a> 후 폴더 덮어쓰기 → chrome://extensions에서 ↻';
      }
    }
  } catch (e) { /* 서버 미접속 등 — 조용히 무시 */ }
}

function loadServerUrl() {
  try {
    chrome.storage.local.get(["serverUrl", "memberCode", "lastResult"], (r) => {
      if (els.serverUrl) els.serverUrl.value = (r && r.serverUrl) || DEFAULT_SERVER;
      if (els.memberCode) els.memberCode.value = (r && r.memberCode) || "";
      const lr = r && r.lastResult;
      if (lr && lr.text && Date.now() - (lr.ts || 0) < 30 * 60 * 1000) {
        showResultBanner(lr.text, !!lr.ok, lr.ts);
      }
    });
  } catch {
    if (els.serverUrl) els.serverUrl.value = DEFAULT_SERVER;
  }
}

function memberCode() {
  return (els.memberCode && els.memberCode.value || "").trim();
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
  if (els.sendCookiesBtn) els.sendCookiesBtn.addEventListener("click", sendIgCookies);
  if (els.serverUrl) els.serverUrl.addEventListener("change", () => {
    try { chrome.storage.local.set({ serverUrl: serverUrl() }); } catch {}
  });
  if (els.memberCode) els.memberCode.addEventListener("change", () => {
    try { chrome.storage.local.set({ memberCode: memberCode() }); } catch {}
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

async function sendIgCookies() {
  if (!memberCode()) return setStatus("회원코드를 먼저 입력하세요");
  setStatus("인스타 쿠키 보내는 중...");
  const r = await send({ type: "SEND_IG_COOKIES", serverUrl: serverUrl(), memberCode: memberCode() });
  if (r && r.ok) setResult("🍪 쿠키 전송됨 — 이제 대본 추출이 됩니다", true);
  else setResult((r && r.error) || "쿠키 전송 실패", false);
}

async function sendToLocalApp() {
  // 보내기는 항상 '전체' 전송 — 조회수 필터로 캐러셀(조회수 0) 등이 몰래 빠지지 않게
  if (!items.length) return setResult("보낼 수집 항목이 없어요", false);

  const account = cleanAccountName(els.accountInput.value.trim()) || guessAccountFromItems(items) || "shortform";
  setStatus("짤공장으로 전송 중...");

  if (!memberCode()) return setResult("회원코드(짤공장 접속코드)를 입력하세요", false);
  const response = await send({ type: "SEND_TO_LOCAL_APP", items, account, serverUrl: serverUrl(), memberCode: memberCode() });
  if (!response || (response.error && !response.fallback)) return setResult((response && response.error) || "응답 없음 — 확장 새로고침 후 재시도", false);
  if (response.fallback) return setResult(response.error || "URL만 전달됨", false);

  const sent = response.count || items.length;
  const extra = (typeof response.added === "number")
    ? ` (신규 ${response.added} · 창고 누적 ${response.total})`
    : (response.total ? ` (창고 누적 ${response.total})` : "");
  setResult(`✅ 전송 완료 — 전체 ${sent}개${extra} → 짤공장 · 수입·수출 · 인스타 수집에서 확인`, true);
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

/* 전송 결과 배너 — 새로고침 루프가 덮어쓰지 않는 전용 표시. 30분간 유지(팝업 다시 열어도 보임) */
function showResultBanner(text, ok, ts) {
  const b = document.getElementById("resultBanner");
  if (!b) return;
  const d = new Date(ts || Date.now());
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  b.style.display = "block";
  b.style.background = ok ? "#1e4635" : "#4a2328";
  b.style.color = ok ? "#7ef0c0" : "#ffb3b8";
  b.textContent = `${text}  (${hhmm})`;
}

function setResult(text, ok) {
  showResultBanner(text, ok, Date.now());
  try { chrome.storage.local.set({ lastResult: { text, ok, ts: Date.now() } }); } catch { /* 무시 */ }
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
