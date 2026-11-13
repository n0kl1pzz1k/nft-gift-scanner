import "@contracts/escrow-types"

// GIFT RADAR per-deal NFT custody escrow.
// Telegram Stars remain in the backend's Telegram merchant balance and double-entry
// Ledger. This contract records a server attestation; it does not custody Stars.
// All contract deployment and interaction in this phase is TON testnet only.

fun EscrowStorage.load() {
    return EscrowStorage.fromCell(contract.getData());
}

fun EscrowStorage.save(self) {
    contract.setData(self.toCell());
}

contract GiftRadarEscrow {
    storage: EscrowStorage
    incomingMessages: AllowedMessage
}

fun sendNftTo(nftItem: address, recipient: address, queryId: uint64) {
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
    transfer.send(
        SEND_MODE_PAY_FEES_SEPARATELY |
        SEND_MODE_BOUNCE_ON_ACTION_FAIL
    );
}

fun onBouncedMessage(in: InMessageBounced) {
    in.bouncedBody.skipBouncedPrefix();
    val bounced = lazy AskToChangeOwnership.fromSlice(in.bouncedBody);
    var storage = lazy EscrowStorage.load();
    val parties = storage.parties.load();
    assert (in.senderAddress == parties.nftItem) throw Errors.BadAsset;
    assert (bounced.queryId == storage.transferQueryId) throw Errors.BadProof;
    if ((storage.state == STATE_TRANSFER_PENDING) | (storage.state == STATE_REFUND_PENDING)) {
        // NFT transfer execution is ambiguous after a bounce. Never auto-retry or settle.
        storage.state = STATE_RECONCILIATION_REQUIRED;
        storage.save();
    }
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        // Acton deployment wrapper's initial empty message; no state transition.
        return;
    }
    val msg = lazy AllowedMessage.fromSlice(in.body);

    match (msg) {
        DeployEscrow => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            val authorities = storage.authorities.load();
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert (!storage.initialized) throw Errors.BadState;
            assert (storage.state == STATE_CREATED) throw Errors.BadState;
            assert (storage.expectedAmount > 0) throw Errors.BadAmount;
            assert (storage.deadline > blockchain.now()) throw Errors.Expired;
            assert (parties.seller != parties.buyer) throw Errors.BadAsset;
            assert (parties.nftItem != parties.seller) throw Errors.BadAsset;
            assert (authorities.arbitrator1 != authorities.arbitrator2) throw Errors.BadDispute;
            storage.initialized = true;
            storage.save();
        }

        NotificationForNewOwner => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == parties.nftItem) throw Errors.BadAsset;
            assert (storage.state == STATE_CREATED) throw Errors.BadState;
            assert (msg.oldOwnerAddress == parties.seller) throw Errors.Unauthorized;
            assert (blockchain.now() < storage.deadline) throw Errors.Expired;
            // Seller's TEP-62 forward amount must fund a later outbound NFT transfer.
            assert (in.valueCoins >= grams("0.12")) throw Errors.NotEnoughGas;
            storage.state = STATE_DEPOSITED;
            storage.save();
        }

        RecordStarsPayment => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            if (storage.state == STATE_PAID) {
                assert (evidence.paymentReferenceHash == msg.telegramChargeHash) throw Errors.BadAmount;
                assert (storage.expectedAmount == msg.amount) throw Errors.BadAmount;
                return;
            }
            assert (storage.state == STATE_DEPOSITED) throw Errors.BadState;
            assert (blockchain.now() < storage.deadline) throw Errors.Expired;
            assert (msg.amount == storage.expectedAmount) throw Errors.BadAmount;
            assert (msg.telegramChargeHash != 0) throw Errors.BadProof;
            evidence.paymentReferenceHash = msg.telegramChargeHash;
            storage.paymentEvidence = evidence.toCell();
            storage.state = STATE_PAID;
            storage.save();
        }

        RequestNftRelease => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == parties.buyer) throw Errors.Unauthorized;
            assert (storage.state == STATE_PAID) throw Errors.BadState;
            assert (blockchain.now() < storage.deadline) throw Errors.Expired;
            assert (contract.getOriginalBalance() >= grams("0.15")) throw Errors.NotEnoughGas;
            storage.transferQueryId = msg.queryId;
            storage.state = STATE_TRANSFER_PENDING;
            storage.save();
            sendNftTo(parties.nftItem, parties.buyer, msg.queryId);
        }

        ConfirmNftDelivery => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.deliveryEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert (storage.state == STATE_TRANSFER_PENDING) throw Errors.BadState;
            assert (msg.buyerOwnershipProofHash != 0) throw Errors.BadProof;
            evidence.buyerOwnershipProofHash = msg.buyerOwnershipProofHash;
            storage.deliveryEvidence = evidence.toCell();
            storage.state = STATE_DELIVERED;
            storage.save();
        }

        MarkSellerSettled => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert (storage.state == STATE_DELIVERED) throw Errors.BadState;
            assert (msg.payoutReferenceHash != 0) throw Errors.BadProof;
            evidence.settlementReferenceHash = msg.payoutReferenceHash;
            storage.paymentEvidence = evidence.toCell();
            storage.state = STATE_SETTLED;
            storage.save();
        }

        RequestRefund => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert ((storage.state == STATE_DEPOSITED) | (storage.state == STATE_PAID)) throw Errors.BadState;
            if (storage.state == STATE_DEPOSITED) {
                assert (blockchain.now() >= storage.deadline) throw Errors.Expired;
            } else {
                // Hash is recorded only after Telegram refundStarPayment succeeds.
                assert (msg.telegramRefundHash != 0) throw Errors.BadProof;
                evidence.refundReferenceHash = msg.telegramRefundHash;
                storage.paymentEvidence = evidence.toCell();
            }
            assert (contract.getOriginalBalance() >= grams("0.15")) throw Errors.NotEnoughGas;
            storage.transferQueryId = msg.queryId;
            storage.state = STATE_REFUND_PENDING;
            storage.save();
            sendNftTo(parties.nftItem, parties.seller, msg.queryId);
        }

        ConfirmNftReturned => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.deliveryEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert (storage.state == STATE_REFUND_PENDING) throw Errors.BadState;
            assert (msg.sellerOwnershipProofHash != 0) throw Errors.BadProof;
            evidence.sellerOwnershipProofHash = msg.sellerOwnershipProofHash;
            storage.deliveryEvidence = evidence.toCell();
            storage.state = STATE_REFUNDED;
            storage.save();
        }

        RaiseDispute => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw Errors.BadState;
            assert ((in.senderAddress == parties.buyer) | (in.senderAddress == parties.seller)) throw Errors.Unauthorized;
            assert (msg.reasonHash != 0) throw Errors.BadDispute;
            assert ((storage.state == STATE_DEPOSITED) |
                    (storage.state == STATE_PAID) |
                    (storage.state == STATE_TRANSFER_PENDING)) throw Errors.BadState;
            storage.disputeReasonHash = msg.reasonHash;
            if (storage.state == STATE_TRANSFER_PENDING) {
                // A transfer may already have executed on the NFT item. Never guess ownership.
                storage.state = STATE_RECONCILIATION_REQUIRED;
            } else {
                storage.preDisputeState = storage.state;
                storage.state = STATE_DISPUTED;
            }
            storage.save();
        }

        RecordStarsRefund => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (in.senderAddress == authorities.paymentAuthority) throw Errors.Unauthorized;
            assert (storage.state == STATE_DISPUTED) throw Errors.BadState;
            assert (storage.preDisputeState == STATE_PAID) throw Errors.BadState;
            assert (msg.telegramRefundHash != 0) throw Errors.BadProof;
            if (evidence.refundReferenceHash != 0) {
                assert (evidence.refundReferenceHash == msg.telegramRefundHash) throw Errors.BadProof;
                return;
            }
            evidence.refundReferenceHash = msg.telegramRefundHash;
            storage.paymentEvidence = evidence.toCell();
            storage.save();
        }

        ApproveDisputeResolution => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            val authorities = storage.authorities.load();
            val payment = storage.paymentEvidence.load();
            var deliveryEvidence = storage.deliveryEvidence.load();
            assert (storage.initialized) throw Errors.BadState;
            assert (storage.state == STATE_DISPUTED) throw Errors.BadState;
            assert ((in.senderAddress == authorities.arbitrator1) | (in.senderAddress == authorities.arbitrator2)) throw Errors.Unauthorized;
            assert ((msg.decision == 1) | (msg.decision == 2)) throw Errors.BadDispute;
            assert (msg.resolutionHash != 0) throw Errors.BadDispute;

            val approval = in.senderAddress == authorities.arbitrator1 ? 1 : 2;
            if (storage.disputeApprovals == 0) {
                storage.disputeApprovals = approval;
                storage.resolutionDecision = msg.decision;
                deliveryEvidence.resolutionHash = msg.resolutionHash;
                storage.deliveryEvidence = deliveryEvidence.toCell();
                storage.save();
                return;
            }

            // Repeated approval from the same arbitrator is idempotent.
            if (storage.disputeApprovals == approval) {
                assert (storage.resolutionDecision == msg.decision) throw Errors.BadDispute;
                assert (deliveryEvidence.resolutionHash == msg.resolutionHash) throw Errors.BadDispute;
                return;
            }

            assert ((storage.disputeApprovals == 1) | (storage.disputeApprovals == 2)) throw Errors.BadDispute;
            assert (storage.resolutionDecision == msg.decision) throw Errors.BadDispute;
            assert (deliveryEvidence.resolutionHash == msg.resolutionHash) throw Errors.BadDispute;

            if (msg.decision == 1) {
                if (storage.preDisputeState == STATE_PAID) {
                    assert (payment.refundReferenceHash != 0) throw Errors.BadProof;
                }
                assert (contract.getOriginalBalance() >= grams("0.15")) throw Errors.NotEnoughGas;
                storage.transferQueryId = msg.queryId;
                storage.state = STATE_REFUND_PENDING;
                storage.disputeApprovals = 3;
                storage.save();
                sendNftTo(parties.nftItem, parties.seller, msg.queryId);
                return;
            }

            // Dismissal resumes the previous state but never releases funds.
            storage.state = storage.preDisputeState;
            storage.disputeApprovals = 3;
            storage.save();
        }

        else => {
            assert (in.body.isEmpty()) throw Errors.UnknownMessage;
        }
    }
}

get fun get_escrow_state(): EscrowStateReply {
    val storage = lazy EscrowStorage.load();
    return {
        dealId: storage.dealId,
        expectedAmount: storage.expectedAmount,
        deadline: storage.deadline,
        initialized: storage.initialized,
        state: storage.state,
        preDisputeState: storage.preDisputeState,
        disputeApprovals: storage.disputeApprovals,
        resolutionDecision: storage.resolutionDecision,
        disputeReasonHash: storage.disputeReasonHash,
        transferQueryId: storage.transferQueryId,
    };
}

get fun get_escrow_parties(): EscrowParties {
    val storage = lazy EscrowStorage.load();
    return storage.parties.load();
}

get fun get_escrow_authorities(): EscrowAuthorities {
    val storage = lazy EscrowStorage.load();
    return storage.authorities.load();
}

get fun get_payment_evidence(): PaymentEvidence {
    val storage = lazy EscrowStorage.load();
    return storage.paymentEvidence.load();
}

get fun get_delivery_evidence(): DeliveryEvidence {
    val storage = lazy EscrowStorage.load();
    return storage.deliveryEvidence.load();
}

