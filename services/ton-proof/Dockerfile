FROM node:22-alpine AS build
WORKDIR /app
COPY services/ton-proof/package.json ./package.json
COPY services/ton-proof/tsconfig.json ./tsconfig.json
RUN npm install --no-audit --no-fund
COPY services/ton-proof/src ./src
RUN npm run build

FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
USER node
EXPOSE 8080
CMD ["npm", "start"]