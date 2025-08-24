# Frontend Dockerfile for Quantiv Next.js (development)
FROM node:18-alpine

WORKDIR /app

# Install OS deps (optional)
RUN apk add --no-cache bash

ENV NODE_ENV=development
EXPOSE 3000

# In dev, mount the source as a volume; install deps at runtime into the mounted volume
CMD ["sh", "-c", "npm install && npm run dev"]
