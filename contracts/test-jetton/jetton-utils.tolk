// Upstream TEP-74 implementation from ton-blockchain/acton-contracts (MIT).
// Source path: jetton-v2.1/contracts/jetton-utils.tolk
// Upstream commit: master snapshot fetched 2026-11-14.
import "sharding"
import "storage"

fun calcDeployedJettonWallet(
    ownerAddress: address,
    minterAddress: address,
    jettonWalletCode: cell,
): AutoDeployAddress {
    val emptyWalletStorage: WalletStorage = {
        jettonBalance: 0,
        ownerAddress,
        minterAddress,
    };

    return {
        workchain: MY_WORKCHAIN,
        stateInit: { code: jettonWalletCode, data: emptyWalletStorage.toCell() },
        toShard: { fixedPrefixLength: SHARD_DEPTH, closeTo: ownerAddress },
    };
}

fun calcAddressOfJettonWallet(
    ownerAddress: address,
    minterAddress: address,
    jettonWalletCode: cell,
): address {
    val jwDeployed = calcDeployedJettonWallet(ownerAddress, minterAddress, jettonWalletCode);
    return jwDeployed.calculateAddress();
}
