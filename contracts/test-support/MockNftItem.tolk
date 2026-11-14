import "@contracts/payment-escrow-types"

struct MockNftItemStorage {
    ownerAddress: address
    collectionAddress: address
    index: uint256
    content: cell
}

struct MockNftDataReply {
    init: int
    index: uint256
    collectionAddress: address
    ownerAddress: address
    content: cell
}

struct (0x5fcc3d14) MockNftTransfer {
    queryId: uint64
    newOwnerAddress: address
    sendExcessesTo: address?
    customPayload: cell?
    forwardGrams: coins
    forwardPayload: RemainingBitsAndRefs
}

contract MockNftItem {
    storage: MockNftItemStorage
    incomingMessages: MockNftTransfer
}

fun MockNftItemStorage.load(): MockNftItemStorage {
    return MockNftItemStorage.fromCell(contract.getData());
}

fun MockNftItemStorage.save(self) {
    contract.setData(self.toCell());
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        return;
    }
    val msg = lazy MockNftTransfer.fromSlice(in.body);
    var storage = lazy MockNftItemStorage.load();
    assert (in.senderAddress == storage.ownerAddress) throw 401;
    assert (msg.newOwnerAddress.getWorkchain() == BASECHAIN) throw 333;

    val oldOwner = storage.ownerAddress;
    storage.ownerAddress = msg.newOwnerAddress;
    storage.save();

    if (msg.forwardGrams != 0) {
        val notification = createMessage({
            bounce: BounceMode.NoBounce,
            dest: msg.newOwnerAddress,
            value: msg.forwardGrams,
            body: PaymentEscrowNftDeposit {
                queryId: msg.queryId,
                oldOwnerAddress: oldOwner,
                payload: msg.forwardPayload,
            },
        });
        notification.send(SEND_MODE_PAY_FEES_SEPARATELY);
    }
}

get fun get_nft_owner(): address {
    val storage = lazy MockNftItemStorage.load();
    return storage.ownerAddress;
}

get fun get_nft_data(): MockNftDataReply {
    val storage = lazy MockNftItemStorage.load();
    return MockNftDataReply {
        init: 1,
        index: storage.index,
        collectionAddress: storage.collectionAddress,
        ownerAddress: storage.ownerAddress,
        content: storage.content,
    };
}
