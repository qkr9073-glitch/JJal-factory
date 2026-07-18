const STORAGE_KEY = "kangarooShortformHunter.items";
const LEGACY_STORAGE_KEY = "instagramReelsNetworkCatcher.items";
const DEBUGGER_VERSION = "1.3";

let watchingTabId = null;
let attached = false;
let items = [];

chrome.runtime.onInstalled.addListener(loadItems);
chrome.runtime.onStartup.addListener(loadItems);
loadItems();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message)
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ error: error.message || String(error) }));
  return true;
});

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!attached || source.tabId !== watchingTabId) return;
  if (method !== "Network.responseReceived") return;
  if (!looksRelevant(params.response?.url || "")) return;

  chrome.debugger.sendCommand({ tabId: watchingTabId }, "Network.getResponseBody", { requestId: params.requestId }, (body) => {
    if (chrome.runtime.lastError || !body?.body) return;
    parseBody(body.body, params.response?.url || "");
  });
});

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId === watchingTabId) {
    attached = false;
    watchingTabId = null;
  }
});

async function handleMessage(message) {
  if (message.type === "OPEN_REELS_TAB") return openSourceTab(message.account);
  if (message.type === "START_WATCH") return startWatch();
  if (message.type === "STOP_WATCH") return stopWatch();
  if (message.type === "CLEAR_ITEMS") return clearItems();
  if (message.type === "GET_STATE") return getState();
  if (message.type === "SEND_TO_LOCAL_APP") return sendToLocalAppFromBackground(message);
  return { error: "Unknown message" };
}

async function openSourceTab(account) {
  const tab = await getActiveTab();
  const url = buildSourceUrl(account, tab.url || "");
  if (!url) return { error: "계정명, 프로필 URL, 채널 URL을 확인하세요." };
  await chrome.tabs.update(tab.id, { url, active: true });
  return { ok: true, url, account: accountFromUrl(url) };
}

async function startWatch() {
  const tab = await getActiveTab();
  if (!isSupportedTab(tab)) return { error: "Instagram 또는 YouTube 탭에서 시작하세요." };

  if (attached && watchingTabId !== tab.id) await stopWatch();

  watchingTabId = tab.id;

  // 디버거 연결 (실패해도 유튜브는 화면 스캔으로 수집 가능)
  let attachNote = "";
  if (!attached) {
    try {
      await attachDebugger(tab.id);
      attached = true;
      await sendDebugger("Network.enable");
      await sendDebugger("Network.setCacheDisabled", { cacheDisabled: true });
    } catch (error) {
      attached = false;
      attachNote = "디버거 미연결: 화면 스캔으로 수집합니다. (스크롤하며 팝업을 열어두세요)";
    }
  } else {
    await sendDebugger("Network.enable").catch(() => {});
    await sendDebugger("Network.setCacheDisabled", { cacheDisabled: true }).catch(() => {});
  }

  let reloaded = false;
  let scanned = 0;
  if (isYoutubeTab(tab)) {
    // 유튜브는 디버거 없이도 DOM 스캔으로 수집
    try {
      scanned = await collectVisibleYoutubeShorts(tab.id);
    } catch (error) {
      return { ...(await getState()), error: "화면 스캔 실패: 페이지를 새로고침(F5)한 뒤 다시 시도하세요." };
    }
  } else if (attached) {
    await chrome.tabs.reload(tab.id);
    reloaded = true;
  } else {
    watchingTabId = null;
    return { ...(await getState()), error: "디버거를 연결할 수 없습니다. 열려 있는 개발자도구(F12)나 다른 디버깅 확장프로그램을 끄고 다시 시도하세요." };
  }

  return {
    ...(await getState()),
    watching: true,
    account: accountFromUrl(tab.url || ""),
    reloaded,
    scanned,
    note: attachNote
  };
}

async function stopWatch() {
  if (attached && watchingTabId != null) {
    await detachDebugger(watchingTabId);
  }
  attached = false;
  watchingTabId = null;
  return getState();
}

async function clearItems() {
  items = [];
  await chrome.storage.local.set({ [STORAGE_KEY]: items, [LEGACY_STORAGE_KEY]: [] });
  return getState();
}

async function getState() {
  await loadItems();
  const tab = await getActiveTab().catch(() => null);
  const watchingThisTab = watchingTabId != null && tab?.id === watchingTabId;
  // 유튜브는 디버거 연결 여부와 무관하게 현재 화면을 다시 스캔
  if (watchingThisTab && isYoutubeTab(tab)) {
    await collectVisibleYoutubeShorts(tab.id).catch(() => {});
  }
  return {
    watching: attached || watchingThisTab,
    tabId: watchingTabId,
    account: accountFromUrl(tab?.url || ""),
    currentUrl: tab?.url || "",
    items
  };
}

async function loadItems() {
  const result = await chrome.storage.local.get([STORAGE_KEY, LEGACY_STORAGE_KEY]);
  items = result[STORAGE_KEY] || result[LEGACY_STORAGE_KEY] || [];
}

async function saveItems() {
  items = rankItems(items);
  await chrome.storage.local.set({ [STORAGE_KEY]: items });
}

function parseBody(bodyText, sourceUrl) {
  let parsed;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return;
  }

  const found = [];
  walk(parsed, (node) => {
    const instagram = normalizeInstagramNode(node);
    if (instagram) found.push({ ...instagram, sourceUrl });

    const youtube = normalizeYoutubeNode(node);
    if (youtube) found.push({ ...youtube, sourceUrl });
  });

  addFoundItems(found);
}

async function collectVisibleYoutubeShorts(tabId) {
  const result = await chrome.scripting.executeScript({
    target: { tabId },
    func: scrapeYoutubeShortsFromPage
  });
  const found = result?.[0]?.result || [];
  await addFoundItems(found);
  return found.length;
}

function scrapeYoutubeShortsFromPage() {
  // 쇼츠(/shorts/)와 롱폼(/watch?v=, youtu.be) 링크를 모두 수집합니다.
  const anchors = Array.from(
    document.querySelectorAll('a[href*="/shorts/"], a[href*="/watch?v="], a[href*="youtu.be/"]')
  );
  // 구/신 레이아웃 모두 지원 (yt-lockup-view-model 은 최신 동영상 카드)
  const VIDEO_CARDS = "ytd-rich-item-renderer, ytd-rich-grid-media, yt-lockup-view-model, ytd-reel-item-renderer, ytd-grid-video-renderer, ytd-video-renderer, ytd-playlist-video-renderer, ytd-compact-video-renderer";
  const found = [];
  const seen = new Set();

  for (const anchor of anchors) {
    let url = "";
    try {
      url = new URL(anchor.getAttribute("href") || anchor.href, location.origin).href;
    } catch {
      continue;
    }

    const card = anchor.closest(VIDEO_CARDS) || anchor.parentElement;

    // 동영상 ID 추출 (쇼츠 / watch / youtu.be 지원)
    let videoId = "";
    const shortsMatch = url.match(/\/shorts\/([A-Za-z0-9_-]{6,})/);
    if (shortsMatch) {
      videoId = shortsMatch[1];
    } else {
      try {
        const u = new URL(url);
        const host = u.hostname.replace(/^www\./, "").replace(/^m\./, "").toLowerCase();
        if (host === "youtu.be") {
          videoId = (u.pathname.split("/").filter(Boolean)[0] || "");
        } else if (host.endsWith("youtube.com") && u.pathname === "/watch") {
          videoId = u.searchParams.get("v") || "";
        }
      } catch {
        // ignore
      }
      // 내비게이션/헤더/필터칩 영역의 watch 링크만 제외하고 나머지는 모두 수집
      // (카드 태그 이름에 의존하지 않아 유튜브 UI가 바뀌어도 잘 동작)
      const CHROME_AREAS = "ytd-masthead, #masthead-container, #guide, tp-yt-app-drawer, ytd-mini-guide-renderer, ytd-feed-filter-chip-bar-renderer, yt-chip-cloud-renderer, ytd-rich-section-renderer";
      if (videoId && anchor.closest(CHROME_AREAS)) continue;
    }

    videoId = (videoId.match(/^[A-Za-z0-9_-]{6,}/) || [""])[0];
    if (!videoId || seen.has(videoId)) continue;
    seen.add(videoId);

    const text = (card?.innerText || anchor.getAttribute("aria-label") || anchor.title || "").replace(/\s+/g, " ").trim();
    const ariaText = (anchor.getAttribute("aria-label") || card?.querySelector("[aria-label]")?.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
    const title = (anchor.getAttribute("title") || anchor.getAttribute("aria-label") || text.split("조회수")[0] || text).replace(/\s+/g, " ").trim();

    found.push({
      platform: "youtube",
      kind: shortsMatch ? "shorts" : "video",
      url: `https://www.youtube.com/watch?v=${videoId}`,
      shortcode: videoId,
      title,
      channel: channelFromPage(),
      viewCount: extractViewCount(text, ariaText),
      likeCount: 0,
      commentCount: 0,
      takenAt: "",
      caption: title
    });
  }

  return found;

  // 조회수만 정확히 추출 (제목 속 숫자/구독자수/배속 배지에 속지 않도록)
  function extractViewCount(cardText, ariaText) {
    const sources = [ariaText, cardText];
    for (const src of sources) {
      if (!src) continue;
      // 한국어: "조회수 1.2만회", "조회수 2천회", "조회수 9,032,050회"
      let m = src.match(/조회수\s*([\d.,]+\s*[천만억]?)\s*회/);
      if (m) return numberFrom(m[1]);
      // 영어: "1.2M views", "12,345 views"
      m = src.match(/([\d.,]+\s*[KMB]?)\s*views/i);
      if (m) return numberFrom(m[1]);
    }
    // 마지막 보루: "N회"(앞에 조회수가 없어도) 형태
    for (const src of sources) {
      if (!src) continue;
      const m = src.match(/([\d.,]+\s*[천만억]?)\s*회/);
      if (m) return numberFrom(m[1]);
    }
    return 0;
  }

  function channelFromPage() {
    const decode = (value) => {
      try {
        return decodeURIComponent(value);
      } catch {
        return value;
      }
    };
    const path = location.pathname.split("/").filter(Boolean);
    const handle = path.find((part) => part.startsWith("@"));
    if (handle) return decode(handle.replace(/^@/, ""));
    const channelText = document.querySelector("ytd-channel-name yt-formatted-string, #channel-name yt-formatted-string")?.textContent;
    return (channelText || "").trim();
  }

  function numberFrom(value) {
    const text = String(value || "").trim().toLowerCase().replace(/,/g, "");
    const unitMatch = text.match(/(\d+(?:\.\d+)?)\s*(억|만|천|[kmb])/i);
    if (unitMatch) {
      const unit = unitMatch[2].toLowerCase();
      const multipliers = { "천": 1000, "만": 10000, "억": 100000000, k: 1000, m: 1000000, b: 1000000000 };
      return Math.round(Number.parseFloat(unitMatch[1]) * (multipliers[unit] || 1));
    }
    const plain = text.match(/\d+(?:\.\d+)?/);
    return plain ? Math.round(Number.parseFloat(plain[0])) : 0;
  }
}

async function addFoundItems(found) {
  if (!found.length) return;

  const map = new Map(items.map((item) => [item.platform + ":" + (item.shortcode || item.url), item]));
  for (const item of found) {
    const key = item.platform + ":" + (item.shortcode || item.url);
    const previous = map.get(key) || {};
    map.set(key, {
      ...previous,
      ...item,
      // 최신 정상값(>0) 우선, 0이면 기존값 유지 (한 번의 오파싱이 영구히 남지 않도록)
      viewCount: (item.viewCount || 0) > 0 ? item.viewCount : (previous.viewCount || 0),
      likeCount: (item.likeCount || 0) > 0 ? item.likeCount : (previous.likeCount || 0),
      commentCount: (item.commentCount || 0) > 0 ? item.commentCount : (previous.commentCount || 0),
      scrapedAt: new Date().toISOString()
    });
  }

  items = [...map.values()];
  await saveItems();
}

async function sendToLocalAppFromBackground(message) {
  const input = Array.isArray(message.items) ? message.items : [];
  if (!input.length) return { error: "보낼 항목이 없습니다." };

  const finalItems = input;
  const urls = finalItems.map((item) => item.url).filter(Boolean);
  const account = String(message.account || guessAccountFromItems(finalItems) || "shortform");
  const payloadItems = finalItems.map(compactItemForLocalApp);

  try {
    const response = await fetchWithTimeout("http://localhost:8777/api/ie/insta/collect", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        influencer: account,
        project: "Short-form Script Research",
        urls,
        items: payloadItems
      })
    }, 7000);

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await chrome.tabs.create({ url: "http://localhost:8777/v2" });
    return {
      ok: true,
      count: urls.length
    };
  } catch {
    const encoded = encodeURIComponent(urls.join("\\n"));
    await chrome.tabs.create({ url: `http://localhost:8777/v2` });
    return { ok: false, fallback: true, count: urls.length, error: "대본앱 API 연결 실패, URL만 전달했습니다." };
  }
}

function compactItemForLocalApp(item) {
  return {
    platform: item.platform || "",
    kind: item.kind || "",
    url: item.url || "",
    shortcode: item.shortcode || "",
    title: item.title || "",
    channel: item.channel || "",
    viewCount: item.viewCount || 0,
    likeCount: item.likeCount || 0,
    commentCount: item.commentCount || 0,
    takenAt: item.takenAt || "",
    caption: item.caption || "",
    imageUrls: Array.isArray(item.imageUrls) ? item.imageUrls.slice(0, 12) : []
  };
}

// 인스타 노드에서 이미지 원본 URL 뽑기(단일 이미지 + 캐러셀 자식). 응답 구조가 버전마다 달라 여러 필드 대응.
function igImageUrls(node) {
  const pick = (n) => {
    if (!n || typeof n !== "object") return "";
    if (n.display_url) return n.display_url;
    const c = (n.image_versions2 && n.image_versions2.candidates) || (n.image_versions && n.image_versions.candidates);
    if (Array.isArray(c) && c[0] && c[0].url) return c[0].url;
    if (Array.isArray(n.display_resources) && n.display_resources.length) return n.display_resources[n.display_resources.length - 1].src || "";
    return "";
  };
  const urls = [];
  const kids =
    (node.edge_sidecar_to_children && Array.isArray(node.edge_sidecar_to_children.edges)
      ? node.edge_sidecar_to_children.edges.map((e) => e && e.node)
      : null) ||
    (Array.isArray(node.carousel_media) ? node.carousel_media : null);
  if (Array.isArray(kids) && kids.length) {
    for (const k of kids) {
      if (k && !k.is_video && !k.video_url) {
        const u = pick(k);
        if (u) urls.push(u);
      }
    }
  } else {
    const u = pick(node);
    if (u) urls.push(u);
  }
  return urls.filter(Boolean);
}

function guessAccountFromItems(list) {
  const youtubeChannel = list.find((item) => item.platform === "youtube" && item.channel)?.channel;
  if (youtubeChannel) return youtubeChannel;
  if (list.some((item) => item.platform === "youtube")) return "youtube_shorts";
  if (list.some((item) => item.platform === "instagram")) return "instagram_reels";
  return "";
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timer);
  }
}

function normalizeInstagramNode(node) {
  if (!node || typeof node !== "object") return null;

  const shortcode =
    node.shortcode ||
    node.code ||
    node.media_code ||
    node.pk_code ||
    node.id_code ||
    node?.media?.code ||
    "";

  if (!shortcode) return null;

  const productType = String(node.product_type || node.media_product_type || node.media_type || node.__typename || "").toLowerCase();
  const isVideo =
    productType.includes("clips") ||
    productType.includes("reel") ||
    productType.includes("video") ||
    node.is_video === true ||
    Boolean(node.play_count || node.view_count || node.video_view_count || node.ig_play_count);

  const likeCount = numberFrom(
    node.like_count ?? node.likeCount ??
    (node.edge_liked_by && node.edge_liked_by.count) ??
    (node.edge_media_preview_like && node.edge_media_preview_like.count)
  );
  const commentCount = numberFrom(
    node.comment_count ?? node.commentCount ??
    (node.edge_media_to_comment && node.edge_media_to_comment.count) ??
    (node.edge_media_to_parent_comment && node.edge_media_to_parent_comment.count)
  );
  const takenAt = node.taken_at || node.taken_at_timestamp || node.device_timestamp || "";

  if (isVideo) {   // 릴스/영상
    return {
      platform: "instagram", kind: "reel",
      url: `https://www.instagram.com/reel/${shortcode}/`,
      shortcode, title: "", channel: "",
      viewCount: numberFrom(node.play_count ?? node.view_count ?? node.video_view_count ?? node.ig_play_count ?? node.playCount),
      likeCount, commentCount, takenAt,
      caption: extractCaption(node), imageUrls: []
    };
  }

  // 이미지 / 캐러셀 (조회수 없음 → 좋아요 기준)
  const imgs = igImageUrls(node);
  if (!imgs.length) return null;   // 이미지도 릴스도 아니면 스킵
  const typename = String(node.__typename || "").toLowerCase();
  const isCarousel = imgs.length > 1 || typename.includes("sidecar") || node.media_type === 8 || Array.isArray(node.carousel_media);
  return {
    platform: "instagram", kind: isCarousel ? "carousel" : "image",
    url: `https://www.instagram.com/p/${shortcode}/`,
    shortcode, title: "", channel: "",
    viewCount: 0, likeCount, commentCount, takenAt,
    caption: extractCaption(node), imageUrls: imgs.slice(0, 12)
  };
}

function normalizeYoutubeNode(node) {
  if (!node || typeof node !== "object") return null;
  if (node.reelItemRenderer) return normalizeYoutubeNode(node.reelItemRenderer);

  const videoId =
    cleanYoutubeId(node.videoId) ||
    cleanYoutubeId(node?.navigationEndpoint?.reelWatchEndpoint?.videoId) ||
    cleanYoutubeId(node?.onTap?.reelWatchEndpoint?.videoId) ||
    cleanYoutubeId(node?.navigationEndpoint?.watchEndpoint?.videoId);

  if (!videoId) return null;

  const endpointUrl = textFrom(node?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url);

  // 쇼츠 신호
  const hasShortsSignal =
    endpointUrl.includes("/shorts/") ||
    Boolean(node?.navigationEndpoint?.reelWatchEndpoint) ||
    Boolean(node.reelWatchEndpoint);

  // 롱폼(일반 영상) 신호
  const hasWatchSignal =
    endpointUrl.includes("/watch") ||
    Boolean(node?.navigationEndpoint?.watchEndpoint);

  // 쇼츠도 롱폼도 아니면 영상이 아님
  if (!hasShortsSignal && !hasWatchSignal) return null;

  const title = textFrom(node.headline || node.title || node.accessibility || node.accessibilityData);
  const channel = textFrom(node.ownerText || node.longBylineText || node.shortBylineText);

  // 조회수는 전용 필드만 사용. 없으면 라벨에서 "조회수 N회 / N views" 부분만 추출
  // (제목 속 숫자 "월 1억" 등을 조회수로 오인하지 않도록)
  let viewText = textFrom(node.viewCountText || node.shortViewCountText);
  if (!viewText) {
    const label = textFrom(node.accessibility || node.accessibilityData || node.metadataText);
    const m = label.match(/조회수\s*[\d.,]+\s*[천만억]?\s*회/) || label.match(/[\d.,]+\s*[KMB]?\s*views/i);
    viewText = m ? m[0] : "";
  }

  return {
    platform: "youtube",
    kind: hasShortsSignal ? "shorts" : "video",
    url: `https://www.youtube.com/watch?v=${videoId}`,
    shortcode: videoId,
    title,
    channel,
    viewCount: numberFrom(viewText),
    likeCount: 0,
    commentCount: 0,
    takenAt: "",
    caption: title
  };
}

function extractCaption(node) {
  if (typeof node.caption === "string") return node.caption;
  if (node.caption?.text) return node.caption.text;
  if (Array.isArray(node.edge_media_to_caption?.edges)) {
    return node.edge_media_to_caption.edges.map((edge) => edge?.node?.text).filter(Boolean).join("\n");
  }
  return "";
}

function textFrom(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value.simpleText === "string") return value.simpleText;
  if (typeof value.label === "string") return value.label;
  if (typeof value.accessibilityData?.label === "string") return value.accessibilityData.label;
  if (Array.isArray(value.runs)) return value.runs.map((run) => run.text || "").join("");
  if (typeof value.text === "string") return value.text;
  return "";
}

function walk(value, callback, seen = new Set()) {
  if (!value || typeof value !== "object" || seen.has(value)) return;
  seen.add(value);
  callback(value);

  if (Array.isArray(value)) {
    for (const item of value) walk(item, callback, seen);
    return;
  }

  for (const item of Object.values(value)) {
    walk(item, callback, seen);
  }
}

function looksRelevant(url) {
  return (
    (/instagram\.com/i.test(url) && /(graphql|api\/v1|clips|reels|media|feed|profile)/i.test(url)) ||
    (/youtube\.com/i.test(url) && /(youtubei\/v1\/browse|youtubei\/v1\/player|shorts|browse)/i.test(url))
  );
}

function rankItems(list) {
  return [...list].sort((a, b) => (b.viewCount || 0) - (a.viewCount || 0));
}

function numberFrom(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;

  const text = String(value || "").trim().toLowerCase().replace(/,/g, "");
  if (!text) return 0;

  const unitMatch = text.match(/(\d+(?:\.\d+)?)\s*(억|만|천|[kmb])/i);
  if (unitMatch) {
    const unit = unitMatch[2].toLowerCase();
    const multipliers = { "천": 1_000, "만": 10_000, "억": 100_000_000, k: 1_000, m: 1_000_000, b: 1_000_000_000 };
    return Math.max(0, Math.round(Number.parseFloat(unitMatch[1]) * (multipliers[unit] || 1)));
  }

  const number = Number.parseFloat((text.match(/\d+(?:\.\d+)?/) || ["0"])[0]);
  return Number.isFinite(number) ? Math.max(0, Math.round(number)) : 0;
}

function cleanYoutubeId(value) {
  const match = String(value || "").match(/^[A-Za-z0-9_-]{6,}$/);
  return match ? match[0] : "";
}

function buildSourceUrl(input, currentUrl = "") {
  const value = String(input || "").trim();
  if (!value) return "";

  if (/youtube\.com|youtu\.be/i.test(value)) {
    return buildYoutubeShortsUrl(value);
  }

  if (/instagram\.com/i.test(value) || !isYoutubeUrl(currentUrl)) {
    return buildInstagramReelsUrl(value);
  }

  return buildYoutubeShortsUrl(value);
}

function buildInstagramReelsUrl(input) {
  const value = String(input || "").trim();
  if (!value) return "";

  try {
    const url = new URL(value.startsWith("http") ? value : `https://www.instagram.com/${value}/`);
    const username = url.pathname.split("/").filter(Boolean)[0]?.replace(/^@/, "");
    return username ? `https://www.instagram.com/${username}/reels/` : "";
  } catch {
    const username = value.replace(/^@/, "").replace(/[^A-Za-z0-9._]/g, "");
    return username ? `https://www.instagram.com/${username}/reels/` : "";
  }
}

function buildYoutubeShortsUrl(input) {
  const value = String(input || "").trim();
  if (!value) return "";

  try {
    const url = new URL(value.startsWith("http") ? value : `https://www.youtube.com/${value}/shorts`);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts[0]?.startsWith("@")) return `https://www.youtube.com/${parts[0]}/shorts`;
    if (parts[0] === "channel" && parts[1]) return `https://www.youtube.com/channel/${parts[1]}/shorts`;
    if (parts[0] === "c" && parts[1]) return `https://www.youtube.com/c/${parts[1]}/shorts`;
    if (parts[0] === "shorts") return url.href;
  } catch {
    const handle = value.replace(/^@?/, "@").replace(/[^@A-Za-z0-9._-]/g, "");
    return handle.length > 1 ? `https://www.youtube.com/${handle}/shorts` : "";
  }

  return "";
}

function accountFromUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^www\./, "").replace(/^m\./, "").toLowerCase();
    const parts = url.pathname.split("/").filter(Boolean);

    if (host.includes("instagram.com")) {
      const first = parts[0] || "";
      if (["accounts", "explore", "p", "reel", "reels", "stories"].includes(first)) return "";
      return first.replace(/^@/, "");
    }

    if (host.includes("youtube.com")) {
      const decode = (value) => { try { return decodeURIComponent(value); } catch { return value; } };
      if (parts[0]?.startsWith("@")) return decode(parts[0].replace(/^@/, ""));
      if (parts[0] === "channel" && parts[1]) return decode(parts[1]);
      return "youtube_shorts";
    }
  } catch {
    return "";
  }

  return "";
}

function isSupportedTab(tab) {
  return isInstagramTab(tab) || isYoutubeTab(tab);
}

function isInstagramTab(tab) {
  return Boolean(tab?.id && tab.url?.startsWith("https://www.instagram.com/"));
}

function isYoutubeTab(tab) {
  return Boolean(tab?.id && isYoutubeUrl(tab.url || ""));
}

function isYoutubeUrl(url) {
  return /^https:\/\/(?:www\.|m\.)?youtube\.com\//i.test(url);
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("활성 탭을 찾을 수 없습니다.");
  return tab;
}

function attachDebugger(tabId) {
  return new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, DEBUGGER_VERSION, () => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message));
      else resolve();
    });
  });
}

function detachDebugger(tabId) {
  return new Promise((resolve) => {
    chrome.debugger.detach({ tabId }, () => resolve());
  });
}

function sendDebugger(command, params = {}) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId: watchingTabId }, command, params, (result) => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message));
      else resolve(result);
    });
  });
}
