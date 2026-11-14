struct MockNftCollectionStorage {
    ownerAddress: address
    itemAddress: address?
    content: cell
}

struct MockNftCollectionDataReply {
    nextItemIndex: uint256
    collectionContent: cell
    ownerAddress: address
}

struct (0x4d4e4931) MockNftCollectionSetItem {
    itemAddress: address
}

contract MockNftCollection {
    storage: MockNftCollectionStorage
    incomingMessages: MockNftCollectionSetItem
}

fun MockNftCollectionStorage.load(): MockNftCollectionStorage {
    return MockNftCollectionStorage.fromCell(contract.getData());
}

fun MockNftCollectionStorage.save(self) {
    contract.setData(self.toCell());
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        return;
    }
    val msg = lazy MockNftCollectionSetItem.fromSlice(in.body);
    var storage = lazy MockNftCollectionStorage.load();
    assert (in.senderAddress == storage.ownerAddress) throw 401;
    assert (storage.itemAddress == null) throw 409;
    assert (msg.itemAddress.getWorkchain() == BASECHAIN) throw 333;
    storage.itemAddress = msg.itemAddress;
    storage.save();
}

get fun get_collection_data(): MockNftCollectionDataReply {
    val storage = lazy MockNftCollectionStorage.load();
    return MockNftCollectionDataReply {
        nextItemIndex: 1,
        collectionContent: storage.content,
        ownerAddress: storage.ownerAddress,
    };
}

get fun get_nft_address_by_index(index: uint256): address {
    val storage = lazy MockNftCollectionStorage.load();
    assert (index == 0) throw 404;
    assert (storage.itemAddress != null) throw 404;
    return storage.itemAddress!;
}
