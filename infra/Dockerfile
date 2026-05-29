FROM node:20-alpine

WORKDIR /workspace

# 1. Install build tools needed for compiling native C++ modules like better-sqlite3
RUN apk add --no-cache python3 make g++

COPY package*.json ./

RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]