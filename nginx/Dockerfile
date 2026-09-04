# Frontend build stage
FROM node:22-alpine AS frontend-builder
WORKDIR /app

# `npm ci` installs exactly the tree pinned in package-lock.json and refuses to
# run if the lock and package.json disagree. Previously only package.json was
# copied and `npm install` resolved dependencies afresh, so two builds of the
# same commit could ship different versions — the lock file was in the
# repository but took no part in the image.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Runtime stage
FROM nginx:1.27.5-alpine
COPY --from=frontend-builder /app/dist /usr/share/nginx/html
COPY nginx/nginx.conf /etc/nginx/nginx.conf
EXPOSE 80 443
