import "@contracts/payment-escrow-types"

// Testnet-only vertical-slice escrow. TON and a platform-allowlisted test Jetton
// are received by this contract. Stars are intentionally not part of this contract.

fun PaymentEscrowStorage.load(): PaymentEscrowStorage {
    return PaymentEscrowStorage.fromCell(contract.getData());
}

fun PaymentEscrowStorage.save(self) {
    contract.setData(self.toCell());
}

contract GiftRadarPaymentEscrow {
    storage: PaymentEscrowStorage
    incomingMessages: PaymentEscrowMessages
}

fun sendEscrowNft(nftItem: address, recipient: address, queryId: uint64) {
    val transfer = createMessage({
        bounce: BounceMode.Only256BitsOfBody,
        dest: nftItem,
        value: grams("0.1"),
        body: AskToChangeOwnership {
            queryId,
            newOwnerAddress: recipient,
            sendExcessesTo: contract.getAddress(),
            customPayload: null,
            forwardGrams: 0,
            forwardPayload: createEmptySlice(),
        },
    });
    transfer.send(SEND_MODE_PAY_FEES_SEPARATELY | SEND_MODE_BOUNCE_ON_ACTION_FAIL);
}

fun isTwoOfThree(mask: uint8): bool {
    return (mask == 3) | (mask == 5) | (mask == 6) | (mask == 7);
}

fun signerBit(sender: address, auth: PaymentEscrowAuthorityConfig): uint8 {
    if (sender == auth.signer1) { return 1; }
    if (sender == auth.signer2) { return 2; }
    if (sender == auth.signer3) { return 4; }
    return 0;
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        return;
    }
    val msg = lazy PaymentEscrowMessages.fromSlice(in.body);
    match (msg) {
        InitializePaymentEscrow => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            val auth = storage.authorities.load();
            assert (in.senderAddress == auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (!storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_CREATED) throw PaymentEscrowErrors.BadState;
            assert (storage.expectedAmount > 0) throw PaymentEscrowErrors.BadAmount;
            assert (storage.deadline > blockchain.now()) throw PaymentEscrowErrors.Expired;
            assert (storage.assetKind == ASSET_TON | storage.assetKind == ASSET_TEST_USDT_JETTON) throw PaymentEscrowErrors.BadAsset;
            assert (parties.seller != parties.buyer) throw PaymentEscrowErrors.BadAsset;
            assert (parties.seller != auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (auth.signer1 != auth.signer2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer1 != auth.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer2 != auth.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.arbitrator1 != auth.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.arbitrator1 != auth.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.arbitrator2 != auth.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer1 != auth.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer1 != auth.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer1 != auth.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer2 != auth.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer2 != auth.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer2 != auth.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer3 != auth.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer3 != auth.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.signer3 != auth.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (parties.seller != auth.signer1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != auth.signer2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != auth.signer3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.signer1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.signer2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.signer3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != auth.arbitrator1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != auth.arbitrator2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != auth.arbitrator3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.arbitrator1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.arbitrator2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.arbitrator3) throw PaymentEscrowErrors.Unauthorized;
            if (storage.assetKind == ASSET_TEST_USDT_JETTON) {
                assert (auth.usdtJettonMaster != auth.escrowUsdtJettonWallet) throw PaymentEscrowErrors.BadAsset;
            }
            storage.initialized = true;
            storage.save();
        }

        PaymentEscrowNftDeposit => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (in.senderAddress == parties.nftItem) throw PaymentEscrowErrors.BadAsset;
            assert (storage.state == V2_CREATED) throw PaymentEscrowErrors.BadState;
            assert (msg.oldOwnerAddress == parties.seller) throw PaymentEscrowErrors.Unauthorized;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (in.valueCoins >= grams("0.12")) throw PaymentEscrowErrors.NotEnoughGas;
            storage.state = V2_DEPOSITED;
            storage.save();
        }

        DepositTonPayment => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.assetKind == ASSET_TON) throw PaymentEscrowErrors.BadAsset;
            assert (in.senderAddress == parties.buyer) throw PaymentEscrowErrors.Unauthorized;
            assert (storage.state == V2_DEPOSITED) throw PaymentEscrowErrors.BadState;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (msg.amount == storage.expectedAmount) throw PaymentEscrowErrors.BadAmount;
            assert (in.valueCoins >= msg.amount) throw PaymentEscrowErrors.BadAmount;
            assert (msg.queryId != 0) throw PaymentEscrowErrors.BadProof;
            storage.paymentQueryId = msg.queryId;
            storage.paymentApprovals = 0;
            storage.state = V2_PAYMENT_PENDING;
            storage.save();
        }

        JettonTransferNotification => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            val auth = storage.authorities.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.assetKind == ASSET_TEST_USDT_JETTON) throw PaymentEscrowErrors.BadAsset;
            assert (in.senderAddress == auth.escrowUsdtJettonWallet) throw PaymentEscrowErrors.Unauthorized;
            assert (msg.sender == parties.buyer) throw PaymentEscrowErrors.Unauthorized;
            assert (storage.state == V2_DEPOSITED) throw PaymentEscrowErrors.BadState;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (msg.amount == storage.expectedAmount) throw PaymentEscrowErrors.BadAmount;
            assert (msg.queryId != 0) throw PaymentEscrowErrors.BadProof;
            storage.paymentQueryId = msg.queryId;
            storage.paymentApprovals = 0;
            storage.state = V2_PAYMENT_PENDING;
            storage.save();
        }

        ConfirmEscrowPayment => {
            var storage = lazy PaymentEscrowStorage.load();
            val auth = storage.authorities.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_PAYMENT_PENDING) throw PaymentEscrowErrors.BadState;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (msg.queryId == storage.paymentQueryId) throw PaymentEscrowErrors.BadProof;
            assert (msg.amount == storage.expectedAmount) throw PaymentEscrowErrors.BadAmount;
            val bit = signerBit(in.senderAddress, auth);
            assert (bit != 0) throw PaymentEscrowErrors.Unauthorized;
            if ((storage.paymentApprovals & bit) != 0) {
                return;
            }
            storage.paymentApprovals = storage.paymentApprovals | bit;
            if (isTwoOfThree(storage.paymentApprovals)) {
                storage.state = V2_PAID;
                storage.releaseAfter = blockchain.now() + 86400;
            }
            storage.save();
        }

        RequestEscrowNftRelease => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (in.senderAddress == parties.buyer) throw PaymentEscrowErrors.Unauthorized;
            assert (storage.state == V2_PAID) throw PaymentEscrowErrors.BadState;
            assert (blockchain.now() >= storage.releaseAfter) throw PaymentEscrowErrors.Expired;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (contract.getOriginalBalance() >= grams("0.15")) throw PaymentEscrowErrors.NotEnoughGas;
            storage.state = V2_TRANSFER_PENDING;
            storage.save();
            sendEscrowNft(parties.nftItem, parties.buyer, msg.queryId);
        }

        ConfirmEscrowNftDelivery => {
            var storage = lazy PaymentEscrowStorage.load();
            val auth = storage.authorities.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_TRANSFER_PENDING) throw PaymentEscrowErrors.BadState;
            assert (msg.buyerOwnershipProofHash != 0) throw PaymentEscrowErrors.BadProof;
            val bit = signerBit(in.senderAddress, auth);
            assert (bit != 0) throw PaymentEscrowErrors.Unauthorized;
            if (storage.deliveryApprovals == 0) {
                storage.deliveryProofHash = msg.buyerOwnershipProofHash;
            } else {
                assert (storage.deliveryProofHash == msg.buyerOwnershipProofHash) throw PaymentEscrowErrors.BadProof;
            }
            if ((storage.deliveryApprovals & bit) == 0) {
                storage.deliveryApprovals = storage.deliveryApprovals | bit;
            }
            if (isTwoOfThree(storage.deliveryApprovals)) {
                storage.state = V2_DELIVERED;
            }
            storage.save();
        }

        RaisePaymentEscrowDispute => {
            var storage = lazy PaymentEscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert ((in.senderAddress == parties.buyer) | (in.senderAddress == parties.seller)) throw PaymentEscrowErrors.Unauthorized;
            assert (msg.reasonHash != 0) throw PaymentEscrowErrors.BadProof;
            assert ((storage.state == V2_DEPOSITED) | (storage.state == V2_PAYMENT_PENDING) | (storage.state == V2_PAID)) throw PaymentEscrowErrors.BadState;
            storage.state = V2_DISPUTED;
            storage.save();
        }

        ApprovePaymentEscrowDispute => {
            var storage = lazy PaymentEscrowStorage.load();
            val auth = storage.authorities.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_DISPUTED) throw PaymentEscrowErrors.BadState;
            assert ((in.senderAddress == auth.arbitrator1) | (in.senderAddress == auth.arbitrator2) | (in.senderAddress == auth.arbitrator3)) throw PaymentEscrowErrors.Unauthorized;
            assert ((msg.decision == 1) | (msg.decision == 2)) throw PaymentEscrowErrors.BadProof;
            assert (msg.resolutionHash != 0) throw PaymentEscrowErrors.BadProof;
            // Dispute voting is a separate 2-of-3 contour; first implementation records
            // evidence only. Automated final-deadline custody handoff is added after E2E.
            return;
        }

        else => {
            assert (in.body.isEmpty()) throw PaymentEscrowErrors.BadState;
        }
    }
}

get fun get_payment_escrow_state(): PaymentEscrowStateReply {
    val storage = lazy PaymentEscrowStorage.load();
    return {
        dealId: storage.dealId,
        expectedAmount: storage.expectedAmount,
        deadline: storage.deadline,
        releaseAfter: storage.releaseAfter,
        assetKind: storage.assetKind,
        initialized: storage.initialized,
        state: storage.state,
        paymentQueryId: storage.paymentQueryId,
        paymentApprovals: storage.paymentApprovals,
        deliveryApprovals: storage.deliveryApprovals,
        deliveryProofHash: storage.deliveryProofHash,
    };
}

get fun get_payment_escrow_authorities(): PaymentEscrowAuthorityConfig {
    val storage = lazy PaymentEscrowStorage.load();
    return storage.authorities.load();
}
