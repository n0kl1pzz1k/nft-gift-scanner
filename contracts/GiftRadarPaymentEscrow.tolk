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
            forwardGrams: grams("0.000000001"),
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    });
    transfer.send(SEND_MODE_PAY_FEES_SEPARATELY | SEND_MODE_BOUNCE_ON_ACTION_FAIL);
}

fun isTwoOfThree(mask: uint8): bool {
    return (mask == 3) | (mask == 5) | (mask == 6) | (mask == 7);
}

fun signerBit(sender: address, signers: PaymentEscrowSignerSet): uint8 {
    if (sender == signers.signer1) { return 1; }
    if (sender == signers.signer2) { return 2; }
    if (sender == signers.signer3) { return 4; }
    return 0;
}

fun onBouncedMessage(in: InMessageBounced) {
    in.bouncedBody.skipBouncedPrefix();
    val bounced = lazy AskToChangeOwnership.fromSlice(in.bouncedBody);
    var storage = lazy PaymentEscrowStorage.load();
    val parties = storage.parties.load();
    assert (in.senderAddress == parties.nftItem) throw PaymentEscrowErrors.BadAsset;
    assert (bounced.queryId == storage.transferQueryId) throw PaymentEscrowErrors.BadProof;
    if (storage.state == V2_TRANSFER_PENDING) {
        storage.state = V2_RECONCILIATION_REQUIRED;
        storage.save();
    }
    assert (bounced.queryId != 0) throw PaymentEscrowErrors.BadProof;
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
            val signers = auth.signers.load();
            val arbitrators = auth.arbitrators.load();
            val assets = auth.assets.load();
            assert (in.senderAddress == auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (!storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_CREATED) throw PaymentEscrowErrors.BadState;
            assert (storage.expectedAmount > 0) throw PaymentEscrowErrors.BadAmount;
            assert (storage.deadline > blockchain.now()) throw PaymentEscrowErrors.Expired;
            assert (storage.deadline <= blockchain.now() + MAX_DEAL_LIFETIME) throw PaymentEscrowErrors.Expired;
            assert ((storage.assetKind == ASSET_TON) | (storage.assetKind == ASSET_TEST_USDT_JETTON)) throw PaymentEscrowErrors.BadAsset;
            assert (parties.seller != parties.buyer) throw PaymentEscrowErrors.BadAsset;
            assert (parties.seller != auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != auth.platformConfigAuthority) throw PaymentEscrowErrors.Unauthorized;
            assert (signers.signer1 != signers.signer2) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer1 != signers.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer2 != signers.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (arbitrators.arbitrator1 != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (arbitrators.arbitrator1 != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (arbitrators.arbitrator2 != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer1 != arbitrators.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer1 != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer1 != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer2 != arbitrators.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer2 != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer2 != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer3 != arbitrators.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer3 != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (signers.signer3 != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (parties.seller != signers.signer1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != signers.signer2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != signers.signer3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != signers.signer1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != signers.signer2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != signers.signer3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != arbitrators.arbitrator1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != arbitrators.arbitrator2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.seller != arbitrators.arbitrator3) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != arbitrators.arbitrator1) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != arbitrators.arbitrator2) throw PaymentEscrowErrors.Unauthorized;
            assert (parties.buyer != arbitrators.arbitrator3) throw PaymentEscrowErrors.Unauthorized;
            assert (auth.platformConfigAuthority != signers.signer1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.platformConfigAuthority != signers.signer2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.platformConfigAuthority != signers.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.platformConfigAuthority != arbitrators.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.platformConfigAuthority != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.platformConfigAuthority != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != parties.seller) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != parties.buyer) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != signers.signer1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != signers.signer2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != signers.signer3) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != arbitrators.arbitrator1) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != arbitrators.arbitrator2) throw PaymentEscrowErrors.BadQuorum;
            assert (auth.resolverMultisig != arbitrators.arbitrator3) throw PaymentEscrowErrors.BadQuorum;
            if (storage.assetKind == ASSET_TEST_USDT_JETTON) {
                assert (msg.escrowUsdtJettonWallet != null) throw PaymentEscrowErrors.BadAsset;
                assert (assets.usdtJettonMaster != msg.escrowUsdtJettonWallet!) throw PaymentEscrowErrors.BadAsset;
                assert (assets.usdtJettonMaster != parties.buyer) throw PaymentEscrowErrors.BadAsset;
                assert (msg.escrowUsdtJettonWallet! != parties.buyer) throw PaymentEscrowErrors.BadAsset;
                assets.escrowUsdtJettonWallet = msg.escrowUsdtJettonWallet;
                auth.assets = assets.toCell();
                storage.authorities = auth.toCell();
            } else {
                assert (msg.escrowUsdtJettonWallet == null) throw PaymentEscrowErrors.BadAsset;
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
            val assets = auth.assets.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.assetKind == ASSET_TEST_USDT_JETTON) throw PaymentEscrowErrors.BadAsset;
            assert (assets.escrowUsdtJettonWallet != null) throw PaymentEscrowErrors.BadAsset;
            assert (in.senderAddress == assets.escrowUsdtJettonWallet!) throw PaymentEscrowErrors.Unauthorized;
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
            val signers = auth.signers.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_PAYMENT_PENDING) throw PaymentEscrowErrors.BadState;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (msg.queryId == storage.paymentQueryId) throw PaymentEscrowErrors.BadProof;
            assert (msg.amount == storage.expectedAmount) throw PaymentEscrowErrors.BadAmount;
            val bit = signerBit(in.senderAddress, signers);
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
            assert (msg.queryId != 0) throw PaymentEscrowErrors.BadProof;
            assert (blockchain.now() >= storage.releaseAfter) throw PaymentEscrowErrors.Expired;
            assert (blockchain.now() < storage.deadline) throw PaymentEscrowErrors.Expired;
            assert (contract.getOriginalBalance() >= grams("0.15")) throw PaymentEscrowErrors.NotEnoughGas;
            storage.transferQueryId = msg.queryId;
            storage.state = V2_TRANSFER_PENDING;
            storage.save();
            sendEscrowNft(parties.nftItem, parties.buyer, msg.queryId);
        }

        ConfirmEscrowNftDelivery => {
            var storage = lazy PaymentEscrowStorage.load();
            val auth = storage.authorities.load();
            val signers = auth.signers.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_TRANSFER_PENDING) throw PaymentEscrowErrors.BadState;
            assert (msg.buyerOwnershipProofHash != 0) throw PaymentEscrowErrors.BadProof;
            val bit = signerBit(in.senderAddress, signers);
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
            val storage = lazy PaymentEscrowStorage.load();
            val auth = storage.authorities.load();
            val arbitrators = auth.arbitrators.load();
            assert (storage.initialized) throw PaymentEscrowErrors.BadState;
            assert (storage.state == V2_DISPUTED) throw PaymentEscrowErrors.BadState;
            assert ((in.senderAddress == arbitrators.arbitrator1) | (in.senderAddress == arbitrators.arbitrator2) | (in.senderAddress == arbitrators.arbitrator3)) throw PaymentEscrowErrors.Unauthorized;
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
        transferQueryId: storage.transferQueryId,
        paymentApprovals: storage.paymentApprovals,
        deliveryApprovals: storage.deliveryApprovals,
        deliveryProofHash: storage.deliveryProofHash,
    };
}

get fun get_payment_escrow_authorities(): PaymentEscrowAuthorityConfig {
    val storage = lazy PaymentEscrowStorage.load();
    return storage.authorities.load();
}
