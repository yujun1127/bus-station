# ─────────────────────────────────────────
# web-server/Dockerfile
# Nginx 웹 서버 컨테이너
# ─────────────────────────────────────────

FROM nginx:1.25-alpine

LABEL maintainer="weather-dashboard"
LABEL description="Nginx static file server for weather dashboard"

# 커스텀 Nginx 설정 복사
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 기본 index.html (data-generator가 덮어쓰기 전까지 표시)
COPY loading.html /usr/share/nginx/html/index.html

# Nginx가 포어그라운드로 실행되도록 설정
CMD ["nginx", "-g", "daemon off;"]
