FROM node:20-alpine

WORKDIR /workspace
COPY package*.json ./
RUN npm ci --only=production
COPY . .
WORKDIR /workspace
EXPOSE 3000
CMD ["npm","run","start"]
