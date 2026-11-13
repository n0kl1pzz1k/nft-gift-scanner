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

const knownWallets: Array<{ codeHash: Buffer; load: Loader }> = [
  { contract: WalletContractV1R1, load: loadV1 },
  { contract: WalletContractV1R2, load: loadV1 },
  { contract: WalletContractV1R3, load: loadV1 },
  { contract: WalletContractV2R1, load: loadV2 },
  { contract: WalletContractV2R2, load: loadV2 },
  { contract: WalletContractV3R1, load: loadV3 },
  { contract: WalletContractV3R2, load: loadV3 },
  { contract: WalletContractV4, load: loadV4 },
  { contract: WalletContractV5R1, load: loadV5 }
].map(({ contract, load }) => ({
  codeHash: contract.create({
    workchain: 0,
    publicKey: Buffer.alloc(32),
    ...(contract === WalletContractV5R1 ? { walletId: { networkGlobalId: -239 } } : {})
  } as never).init.code.hash(),
  load
}));

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
