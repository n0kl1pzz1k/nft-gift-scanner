// GIFT RADAR per-deal NFT custody escrow.
// Telegram Stars remain in the backend's Telegram merchant balance and double-entry
// Ledger. This contract records a server attestation; it does not custody Stars.
// All contract deployment and interaction in this phase is TON testnet only.

const ERR_UNAUTHORIZED = 1001;
const ERR_BAD_STATE = 1002;
const ERR_BAD_ASSET = 1003;
const ERR_BAD_AMOUNT = 1004;
const ERR_EXPIRED = 1005;
const ERR_BAD_PROOF = 1006;
const ERR_BAD_DISPUTE = 1007;
const ERR_UNKNOWN_MESSAGE = 1008;
const ERR_NOT_ENOUGH_GAS = 1009;

const STATE_CREATED = 0;
const STATE_DEPOSITED = 1;
const STATE_PAID = 2;
const STATE_TRANSFER_PENDING = 3;
const STATE_DELIVERED = 4;
const STATE_SETTLED = 5;
const STATE_REFUNDED = 6;
const STATE_DISPUTED = 7;
const STATE_RECONCILIATION_REQUIRED = 8;
const STATE_REFUND_PENDING = 9;

// Split the storage into referenced cells. TON cells are limited to 1023 bits;
// keeping each record below that limit is a compile-time invariant.
struct EscrowParties {
    seller: address
    buyer: address
    nftItem: address
}

struct EscrowAuthorities {
    paymentAuthority: address
    arbitrator1: address
    arbitrator2: address
}

struct PaymentEvidence {
    paymentReferenceHash: uint256
    refundReferenceHash: uint256
    settlementReferenceHash: uint256
}

struct DeliveryEvidence {
    buyerOwnershipProofHash: uint256
    sellerOwnershipProofHash: uint256
    resolutionHash: uint256
}

struct EscrowStorage {
    dealId: uint256
    expectedAmount: uint128
    deadline: uint64
    initialized: bool
    state: uint8
    preDisputeState: uint8
    disputeApprovals: uint8
    resolutionDecision: uint8
    disputeReasonHash: uint256
    transferQueryId: uint64
    parties: Cell<EscrowParties>
    authorities: Cell<EscrowAuthorities>
    disputeReasonHash: uint256
    paymentEvidence: Cell<PaymentEvidence>
    deliveryEvidence: Cell<DeliveryEvidence>
}

fun EscrowStorage.load() {
    return EscrowStorage.fromCell(contract.getData());
}

fun EscrowStorage.save(self) {
    contract.setData(self.toCell());
}

// TEP-62 NFT item notification: ownership has been assigned to this contract.
struct (0x05138d91) NotificationForNewOwner {
    queryId: uint64
    oldOwnerAddress: address
    payload: RemainingBitsAndRefs
}

// TEP-62 NFT item transfer request.
struct (0x5fcc3d14) AskToChangeOwnership {
    queryId: uint64
    newOwnerAddress: address
    sendExcessesTo: address?
    customPayload: cell?
    forwardGrams: coins
    forwardPayload: RemainingBitsAndRefs
}

struct (0x47524401) DeployEscrow {
    queryId: uint64
}

struct (0x47524402) RecordStarsPayment {
    queryId: uint64
    amount: uint128
    telegramChargeHash: uint256
}

struct (0x47524403) RequestNftRelease {
    queryId: uint64
}

struct (0x47524404) ConfirmNftDelivery {
    queryId: uint64
    buyerOwnershipProofHash: uint256
}

struct (0x47524405) MarkSellerSettled {
    queryId: uint64
    payoutReferenceHash: uint256
}

struct (0x47524406) RequestRefund {
    queryId: uint64
    telegramRefundHash: uint256
}

struct (0x47524407) ConfirmNftReturned {
    queryId: uint64
    sellerOwnershipProofHash: uint256
}

struct (0x47524408) RaiseDispute {
    queryId: uint64
    reasonHash: uint256
}

struct (0x47524409) RecordStarsRefund {
    queryId: uint64
    telegramRefundHash: uint256
}

struct (0x4752440a) ApproveDisputeResolution {
    queryId: uint64
    decision: uint8 // 1 = refund buyer and return NFT; 2 = dismiss and resume
    resolutionHash: uint256
}

type AllowedMessage =
    | DeployEscrow
    | NotificationForNewOwner
    | RecordStarsPayment
    | RequestNftRelease
    | ConfirmNftDelivery
    | MarkSellerSettled
    | RequestRefund
    | ConfirmNftReturned
    | RaiseDispute
    | RecordStarsRefund
    | ApproveDisputeResolution

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
    var storage = lazy EscrowStorage.load();
    val parties = storage.parties.load();
    assert (in.senderAddress == parties.nftItem) throw ERR_BAD_ASSET;
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
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (!storage.initialized) throw ERR_BAD_STATE;
            assert (storage.state == STATE_CREATED) throw ERR_BAD_STATE;
            assert (storage.expectedAmount > 0) throw ERR_BAD_AMOUNT;
            assert (storage.deadline > blockchain.now()) throw ERR_EXPIRED;
            assert (parties.seller != parties.buyer) throw ERR_BAD_ASSET;
            assert (parties.nftItem != parties.seller) throw ERR_BAD_ASSET;
            assert (authorities.arbitrator1 != authorities.arbitrator2) throw ERR_BAD_DISPUTE;
            storage.initialized = true;
            storage.save();
        }

        NotificationForNewOwner => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == parties.nftItem) throw ERR_BAD_ASSET;
            assert (storage.state == STATE_CREATED) throw ERR_BAD_STATE;
            assert (msg.oldOwnerAddress == parties.seller) throw ERR_UNAUTHORIZED;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            // Seller's TEP-62 forward amount must fund a later outbound NFT transfer.
            assert (in.valueCoins >= grams("0.12")) throw ERR_NOT_ENOUGH_GAS;
            storage.state = STATE_DEPOSITED;
            storage.save();
        }

        RecordStarsPayment => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            if (storage.state == STATE_PAID) {
                assert (evidence.paymentReferenceHash == msg.telegramChargeHash) throw ERR_BAD_AMOUNT;
                assert (storage.expectedAmount == msg.amount) throw ERR_BAD_AMOUNT;
                return;
            }
            assert (storage.state == STATE_DEPOSITED) throw ERR_BAD_STATE;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            assert (msg.amount == storage.expectedAmount) throw ERR_BAD_AMOUNT;
            assert (msg.telegramChargeHash != 0) throw ERR_BAD_PROOF;
            evidence.paymentReferenceHash = msg.telegramChargeHash;
            storage.paymentEvidence = evidence.toCell();
            storage.state = STATE_PAID;
            storage.save();
        }

        RequestNftRelease => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == parties.buyer) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_PAID) throw ERR_BAD_STATE;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
            storage.transferQueryId = msg.queryId;
            storage.state = STATE_TRANSFER_PENDING;
            storage.save();
            sendNftTo(parties.nftItem, parties.buyer, msg.queryId);
        }

        ConfirmNftDelivery => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.deliveryEvidence.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_TRANSFER_PENDING) throw ERR_BAD_STATE;
            assert (msg.buyerOwnershipProofHash != 0) throw ERR_BAD_PROOF;
            evidence.buyerOwnershipProofHash = msg.buyerOwnershipProofHash;
            storage.deliveryEvidence = evidence.toCell();
            storage.state = STATE_DELIVERED;
            storage.save();
        }

        MarkSellerSettled => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.paymentEvidence.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_DELIVERED) throw ERR_BAD_STATE;
            assert (msg.payoutReferenceHash != 0) throw ERR_BAD_PROOF;
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
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert ((storage.state == STATE_DEPOSITED) | (storage.state == STATE_PAID)) throw ERR_BAD_STATE;
            if (storage.state == STATE_DEPOSITED) {
                assert (blockchain.now() >= storage.deadline) throw ERR_EXPIRED;
            } else {
                // Hash is recorded only after Telegram refundStarPayment succeeds.
                assert (msg.telegramRefundHash != 0) throw ERR_BAD_PROOF;
                evidence.refundReferenceHash = msg.telegramRefundHash;
                storage.paymentEvidence = evidence.toCell();
            }
            assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
            storage.state = STATE_REFUND_PENDING;
            storage.save();
            sendNftTo(parties.nftItem, parties.seller, msg.queryId);
        }

        ConfirmNftReturned => {
            var storage = lazy EscrowStorage.load();
            val authorities = storage.authorities.load();
            var evidence = storage.deliveryEvidence.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_REFUND_PENDING) throw ERR_BAD_STATE;
            assert (msg.sellerOwnershipProofHash != 0) throw ERR_BAD_PROOF;
            evidence.sellerOwnershipProofHash = msg.sellerOwnershipProofHash;
            storage.deliveryEvidence = evidence.toCell();
            storage.state = STATE_REFUNDED;
            storage.save();
        }

        RaiseDispute => {
            var storage = lazy EscrowStorage.load();
            val parties = storage.parties.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert ((in.senderAddress == parties.buyer) | (in.senderAddress == parties.seller)) throw ERR_UNAUTHORIZED;
            assert (msg.reasonHash != 0) throw ERR_BAD_DISPUTE;
            assert ((storage.state == STATE_DEPOSITED) |
                    (storage.state == STATE_PAID) |
                    (storage.state == STATE_TRANSFER_PENDING)) throw ERR_BAD_STATE;
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
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == authorities.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_DISPUTED) throw ERR_BAD_STATE;
            assert (storage.preDisputeState == STATE_PAID) throw ERR_BAD_STATE;
            assert (msg.telegramRefundHash != 0) throw ERR_BAD_PROOF;
            if (evidence.refundReferenceHash != 0) {
                assert (evidence.refundReferenceHash == msg.telegramRefundHash) throw ERR_BAD_PROOF;
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
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (storage.state == STATE_DISPUTED) throw ERR_BAD_STATE;
            assert ((in.senderAddress == authorities.arbitrator1) | (in.senderAddress == authorities.arbitrator2)) throw ERR_UNAUTHORIZED;
            assert ((msg.decision == 1) | (msg.decision == 2)) throw ERR_BAD_DISPUTE;
            assert (msg.resolutionHash != 0) throw ERR_BAD_DISPUTE;

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
                assert (storage.resolutionDecision == msg.decision) throw ERR_BAD_DISPUTE;
                assert (deliveryEvidence.resolutionHash == msg.resolutionHash) throw ERR_BAD_DISPUTE;
                return;
            }

            assert ((storage.disputeApprovals == 1) | (storage.disputeApprovals == 2)) throw ERR_BAD_DISPUTE;
            assert (storage.resolutionDecision == msg.decision) throw ERR_BAD_DISPUTE;
            assert (deliveryEvidence.resolutionHash == msg.resolutionHash) throw ERR_BAD_DISPUTE;

            if (msg.decision == 1) {
                if (storage.preDisputeState == STATE_PAID) {
                    assert (payment.refundReferenceHash != 0) throw ERR_BAD_PROOF;
                }
                assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
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
            assert (in.body.isEmpty()) throw ERR_UNKNOWN_MESSAGE;
        }
    }
}

struct EscrowStateReply {
    dealId: uint256
    expectedAmount: uint128
    deadline: uint64
    initialized: bool
    state: uint8
    preDisputeState: uint8
    disputeApprovals: uint8
    resolutionDecision: uint8
    transferQueryId: uint64
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

