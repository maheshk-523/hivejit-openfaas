FROM node:22-alpine

WORKDIR /srv
COPY server.js workload.js ./

ENV NODE_ENV=production \
    PORT=8080 \
    V8_CACHE_MODE=baseline \
    WORKLOAD=lusearch \
    FUNCTION_COUNT=3000 \
    ROUNDS=10000 \
    REQUEST_INVOCATIONS=8

EXPOSE 8080
CMD ["node", "/srv/server.js"]
