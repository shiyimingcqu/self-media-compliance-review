FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci
COPY frontend ./frontend
# The two builds pin the role exactly like dev:brand / dev:creator.
RUN npm run build --workspace frontend -- --mode brand \
    && mv frontend/dist /brand \
    && npm run build --workspace frontend -- --mode creator \
    && mv frontend/dist /creator

FROM nginx:1.28-alpine
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx/api.conf /etc/nginx/review-api.conf
COPY --from=build /brand /usr/share/nginx/brand
COPY --from=build /creator /usr/share/nginx/creator
EXPOSE 8080 8081
