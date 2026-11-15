import Fastify from "fastify";
import nacl from "tweetnacl";
import { getSecureRandomBytes, sha256 } from "@ton/crypto";
import {
  Address, Cell, contractAddress, loadStateInit, TonClient4
} from "@ton/ton";
import { Buffer } from "node:buffer";
import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { extractKnownWalletPublicKey, WALLET_TG_CODE_HASH } from "./wallets.js";

const app = Fastify({ logger: true, bodyLimit: 16 * 1024 });
const internalToken = process.env.TON_PROOF_INTERNAL_TOKEN ?? "";
const allowedDomains = new Set(
  (process.env.TON_PROOF_ALLOWED_DOMAINS ?? "")
    .split(",").map((value) => value.trim().toLowerCase()).filter(Boolean)
);

// One-use challenges live only in this private, single-replica sidecar process.
// A restart invalidates every outstanding challenge, so replay fails closed.
const challenges = new Map<string, { userId: string; expiresAt: number }>();
const mainnetEndpoint = process.env.TON_MAINNET_ENDPOINT ?? "https://mainnet-v4.tonhubapi.com";
const testnetEndpoint = process.env.TON_TESTNET_ENDPOINT ?? "https://testnet-v4.tonhubapi.com";

const proofSchema = z.object({
  timestamp: z.union([z.number().int(), z.string().regex(/^\d+$/)]).transform(Number),
  domain: z.object({
    lengthBytes: z.number().int().nonnegative().max(128),
    value: z.string().min(1).max(128)
  }),
  signature: z.string().min(1).max(256),
  payload: z.string().min(1).max(128)
});
const verifySchema = z.object({
  userId: z.string().regex(/^\d{1,24}$/),
  address: z.string().min(3).max(128),
  network: z.enum(["-239", "-3"]),
  public_key: z.string().regex(/^[0-9a-fA-F]{64}$/).optional(),
  proof: proofSchema,
  state_init: z.string().min(1).max(12000)
});

function authorized(value: string | undefined): boolean {
  if (!internalToken || !value) return false;
  const a = Buffer.from(value);
  const b = Buffer.from(internalToken);
  return a.length === b.length && timingSafeEqual(a, b);
}

app.addHook("preHandler", async (request, reply) => {
  if (request.url === "/health") return;
  if (!authorized(request.headers["x-gift-radar-internal"] as string | undefined)) {
    return reply.code(401).send({ error: "unauthorized" });
  }
});

async function getWalletTgPublicKey(address: Address, network: "-239" | "-3", stateInitCodeHash: string): Promise<Buffer> {
  if (stateInitCodeHash !== WALLET_TG_CODE_HASH) throw new Error("WalletTg StateInit code hash mismatch");
  const client = new TonClient4({ endpoint: network === "-239" ? mainnetEndpoint : testnetEndpoint });
  const provider = client.provider(address, null);
  const account = await provider.getState();
  if (account.state.type !== "active" || !account.state.code) {
    throw new Error("WalletTg account is not active");
  }
  // TonClient4 already returns the account code as a BOC buffer.
  const liveCode = Cell.fromBoc(account.state.code);
  if (liveCode.length !== 1 || liveCode[0].hash().toString("hex") !== WALLET_TG_CODE_HASH) {
    throw new Error("WalletTg live code hash mismatch");
  }
  const result = await provider.get("get_public_key", []);
  const key = result.stack.readBigNumber();
  if (key < 0n || key >= (1n << 256n)) throw new Error("Invalid WalletTg public key");
  return Buffer.from(key.toString(16).padStart(64, "0"), "hex");
}

async function verifyProof(input: z.infer<typeof verifySchema>): Promise<Buffer | null> {
  const now = Math.floor(Date.now() / 1000);
  const proof = input.proof;
  if (Math.abs(now - proof.timestamp) > 600) return null;
  const domain = proof.domain.value.toLowerCase();
  if (!allowedDomains.has(domain) || !domain.includes(".")) return null;
  const domainBytes = Buffer.from(proof.domain.value, "utf8");
  if (domainBytes.length !== proof.domain.lengthBytes) return null;

  const payloadBytes = Buffer.from(proof.payload, "utf8");
  if (payloadBytes.length > 128) return null;
  const stateInit = loadStateInit(Cell.fromBase64(input.state_init).beginParse());
  const address = Address.parse(input.address);
  const derivedAddress = contractAddress(address.workChain, stateInit);
  if (!derivedAddress.equals(address)) return null;

  let publicKey = extractKnownWalletPublicKey(stateInit);
  if (!publicKey) {
    const codeHash = stateInit.code?.hash().toString("hex") ?? "";
    if (codeHash !== WALLET_TG_CODE_HASH) return null;
    publicKey = await getWalletTgPublicKey(address, input.network, codeHash);
  }
  if (input.public_key && !publicKey.equals(Buffer.from(input.public_key, "hex"))) return null;

  const signature = Buffer.from(proof.signature, "base64");
  if (signature.length !== 64) return null;
  const workchain = Buffer.alloc(4);
  workchain.writeInt32BE(address.workChain, 0);
  const timestamp = Buffer.alloc(8);
  timestamp.writeBigUInt64LE(BigInt(proof.timestamp), 0);
  const domainLength = Buffer.alloc(4);
  domainLength.writeUInt32LE(domainBytes.length, 0);
  const message = Buffer.concat([
    Buffer.from("ton-proof-item-v2/", "utf8"),
    workchain,
    address.hash,
    domainLength,
    domainBytes,
    timestamp,
    payloadBytes
  ]);
  const messageHash = Buffer.from(await sha256(message));
  const fullMessage = Buffer.concat([
    Buffer.from([0xff, 0xff]),
    Buffer.from("ton-connect", "utf8"),
    messageHash
  ]);
  const digest = Buffer.from(await sha256(fullMessage));
  const valid = nacl.sign.detached.verify(new Uint8Array(digest), new Uint8Array(signature), new Uint8Array(publicKey));
  return valid ? publicKey : null;
}

app.get("/health", async () => ({ status: "ok", service: "gift-radar-ton-proof" }));

app.post("/v1/challenges", async (request, reply) => {
  const body = z.object({ userId: z.string().regex(/^\\d{1,24}$/) }).safeParse(request.body);
  if (!body.success) return reply.code(400).send({ error: "invalid_request" });
  const now = Math.floor(Date.now() / 1000);
  for (const [payload, challenge] of challenges) {
    if (challenge.expiresAt <= now) challenges.delete(payload);
  }
  while (challenges.size >= 10000) {
    const oldest = challenges.keys().next().value;
    if (oldest === undefined) break;
    challenges.delete(oldest);
  }
  const payload = Buffer.from(await getSecureRandomBytes(32)).toString("hex");
  const expiresAt = now + 600;
  challenges.set(payload, { userId: body.data.userId, expiresAt });
  return { payload, expiresAt };
});

app.post("/v1/verify", async (request, reply) => {
  const parsed = verifySchema.safeParse(request.body);
  if (!parsed.success) return reply.code(400).send({ error: "invalid_request" });
  let publicKey: Buffer | null = null;
  try { publicKey = await verifyProof(parsed.data); } catch (error) {
    request.log.warn({ error }, "TON proof validation failed");
  }
  if (!publicKey) return reply.code(401).send({ error: "invalid_proof" });

  const challenge = challenges.get(parsed.data.proof.payload);
  const now = Math.floor(Date.now() / 1000);
  if (!challenge || challenge.userId !== parsed.data.userId || challenge.expiresAt <= now) {
    return reply.code(409).send({ error: "challenge_expired_or_already_used" });
  }
  // Delete synchronously after all cryptographic checks. A challenge can be consumed once.
  challenges.delete(parsed.data.proof.payload);
  return {
    verified: true,
    address: Address.parse(parsed.data.address).toRawString(),
    network: parsed.data.network
  };
});

if (!internalToken || allowedDomains.size === 0) {
  app.log.error("TON_PROOF_INTERNAL_TOKEN and TON_PROOF_ALLOWED_DOMAINS are required");
  process.exit(1);
}
await app.listen({ port: Number(process.env.TON_PROOF_PORT ?? 8081), host: "127.0.0.1" });
