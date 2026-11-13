import {
  Slice,
  StateInit,
  WalletContractV1R1,
  WalletContractV1R2,
  WalletContractV1R3,
  WalletContractV2R1,
  WalletContractV2R2,
  WalletContractV3R1,
  WalletContractV3R2,
  WalletContractV4,
  WalletContractV5R1
} from "@ton/ton";
import { Buffer } from "node:buffer";

type Loader = (slice: Slice) => Buffer;
type WalletContractFactory = any;

function loadV1(slice: Slice): Buffer {
  slice.loadUint(32);
  return slice.loadBuffer(32);
}
function loadV2(slice: Slice): Buffer {
  slice.loadUint(32);
  return slice.loadBuffer(32);
}
function loadV3(slice: Slice): Buffer {
  slice.loadUint(32);
  slice.loadUint(32);
  return slice.loadBuffer(32);
}
function loadV4(slice: Slice): Buffer {
  slice.loadUint(32);
  slice.loadUint(32);
  const key = slice.loadBuffer(32);
  slice.loadMaybeRef();
  return key;
}
function loadV5(slice: Slice): Buffer {
  slice.loadBoolean();
  slice.loadUint(32);
  slice.loadUint(32);
  const key = slice.loadBuffer(32);
  slice.loadMaybeRef();
  return key;
}

const knownWallets: Array<{ codeHash: Buffer; load: Loader }> = [];
function registerWallet(contract: WalletContractFactory, load: Loader, walletId?: unknown): void {
  const args: Record<string, unknown> = {
    workchain: 0,
    publicKey: Buffer.alloc(32)
  };
  if (walletId !== undefined) args.walletId = walletId;
  knownWallets.push({ codeHash: contract.create(args).init.code.hash(), load });
}

registerWallet(WalletContractV1R1, loadV1);
registerWallet(WalletContractV1R2, loadV1);
registerWallet(WalletContractV1R3, loadV1);
registerWallet(WalletContractV2R1, loadV2);
registerWallet(WalletContractV2R2, loadV2);
registerWallet(WalletContractV3R1, loadV3);
registerWallet(WalletContractV3R2, loadV3);
registerWallet(WalletContractV4, loadV4);
registerWallet(WalletContractV5R1, loadV5, { networkGlobalId: -239 });

export function extractKnownWalletPublicKey(stateInit: StateInit): Buffer | null {
  if (!stateInit.code || !stateInit.data) return null;
  const codeHash = stateInit.code.hash();
  for (const wallet of knownWallets) {
    if (!wallet.codeHash.equals(codeHash)) continue;
    const publicKey = wallet.load(stateInit.data.beginParse());
    if (publicKey.length !== 32) throw new Error("Invalid wallet public key length");
    return publicKey;
  }
  return null;
}

export const WALLET_TG_CODE_HASH =
  "9149ae51c1e4689710cebf7830297b16acfbadb363a920a537893e7ffeeca768";
