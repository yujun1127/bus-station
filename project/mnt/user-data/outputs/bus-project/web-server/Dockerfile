FROM nginx:1.25-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY loading.html /usr/share/nginx/html/index.html

CMD ["nginx", "-g", "daemon off;"]
