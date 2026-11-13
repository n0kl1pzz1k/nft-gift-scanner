import "@contracts/payment-escrow-types"

struct MockJettonWalletStorage {
    ownerAddress: address
    platformConfigAuthority: address
    peerWallet: address?
    jettonBalance: coins
}

struct (0x47524a01) SetMockJettonPeer {
    peerWallet: address
}

struct (0x0f8a7ea5) MockJettonTransfer {
    queryId: uint64
    jettonAmount: coins
    destination: address
    responseDestination: address?
    customPayload: cell?
    forwardTonAmount: coins
    forwardPayload: RemainingBitsAndRefs
}

struct (0x178d4519) MockJettonInternalTransfer {
    queryId: uint64
    jettonAmount: coins
    transferInitiator: address
    forwardTonAmount: coins
    forwardPayload: RemainingBitsAndRefs
}

type MockJettonMessages = SetMockJettonPeer | MockJettonTransfer | MockJettonInternalTransfer

contract MockJettonWallet {
    storage: MockJettonWalletStorage
    incomingMessages: MockJettonMessages
}

fun MockJettonWalletStorage.load(): MockJettonWalletStorage {
    return MockJettonWalletStorage.fromCell(contract.getData());
}

fun MockJettonWalletStorage.save(self) {
    contract.setData(self.toCell());
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        return;
    }
    val msg = lazy MockJettonMessages.fromSlice(in.body);
    match (msg) {
        SetMockJettonPeer => {
            var storage = lazy MockJettonWalletStorage.load();
            assert (in.senderAddress == storage.platformConfigAuthority) throw 2101;
            assert (storage.peerWallet == null) throw 2102;
            assert (msg.peerWallet != contract.getAddress()) throw 2103;
            storage.peerWallet = msg.peerWallet;
            storage.save();
        }
        MockJettonTransfer => {
            var storage = lazy MockJettonWalletStorage.load();
            assert (in.senderAddress == storage.ownerAddress) throw 2101;
            assert (storage.peerWallet != null) throw 2102;
            assert (msg.destination == storage.peerWallet) throw 2103;
            assert (msg.jettonAmount > 0) throw 2104;
            assert (storage.jettonBalance >= msg.jettonAmount) throw 2105;
            assert (msg.queryId != 0) throw 2106;
            storage.jettonBalance -= msg.jettonAmount;
            storage.save();

            val internalTransfer = createMessage({
                bounce: BounceMode.Only256BitsOfBody,
                dest: msg.destination,
                value: grams("0.1"),
                body: MockJettonInternalTransfer {
                    queryId: msg.queryId,
                    jettonAmount: msg.jettonAmount,
                    transferInitiator: storage.ownerAddress,
                    forwardTonAmount: msg.forwardTonAmount,
                    forwardPayload: msg.forwardPayload,
                },
            });
            internalTransfer.send(SEND_MODE_PAY_FEES_SEPARATELY | SEND_MODE_BOUNCE_ON_ACTION_FAIL);
        }
        MockJettonInternalTransfer => {
            var storage = lazy MockJettonWalletStorage.load();
            assert (storage.peerWallet != null) throw 2102;
            assert (in.senderAddress == storage.peerWallet) throw 2101;
            assert (msg.jettonAmount > 0) throw 2104;
            assert (msg.queryId != 0) throw 2106;
            storage.jettonBalance += msg.jettonAmount;
            storage.save();

            if (msg.forwardTonAmount != 0) {
                val notification = createMessage({
                    bounce: BounceMode.NoBounce,
                    dest: storage.ownerAddress,
                    value: msg.forwardTonAmount,
                    body: JettonTransferNotification {
                        queryId: msg.queryId,
                        amount: msg.jettonAmount,
                        sender: msg.transferInitiator,
                        forwardPayload: msg.forwardPayload,
                    },
                });
                notification.send(SEND_MODE_PAY_FEES_SEPARATELY);
            }
        }
        else => {
            assert (in.body.isEmpty()) throw 0xFFFF;
        }
    }
}

get fun get_mock_jetton_balance(): coins {
    val storage = lazy MockJettonWalletStorage.load();
    return storage.jettonBalance;
}

get fun get_mock_jetton_owner(): address {
    val storage = lazy MockJettonWalletStorage.load();
    return storage.ownerAddress;
}
