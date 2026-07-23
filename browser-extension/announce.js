// 짤공장 페이지에 확장 설치 여부/버전을 알림(버전 체크 버튼용)
try {
  document.documentElement.setAttribute("data-jjal-ext", chrome.runtime.getManifest().version);
} catch (e) {}
