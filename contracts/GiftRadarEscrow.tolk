// GIFT RADAR per-deal NFT custody escrow.
// This contract deliberately does not custody Telegram Stars: Stars are confirmed
// by the Telegram webhook and tracked as liabilities in the backend double-entry Ledger.
// No private keys or signing material are stored here.

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

struct EscrowStorage {
    dealId: uint256
    seller: address
    buyer: address
    nftItem: address
    paymentAuthority: address
    arbitrator1: address
    arbitrator2: address
    expectedAmount: uint128
    deadline: uint64
    initialized: bool
    state: uint8
    preDisputeState: uint8
    disputeApprovals: uint8
    resolutionDecision: uint8
    paymentReferenceHash: uint256
    refundReferenceHash: uint256
    deliveryProofHash: uint256
    returnProofHash: uint256
    settlementReferenceHash: uint256
    disputeReasonHash: uint256
    resolutionHash: uint256
    transferQueryId: uint64
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

fun sendNftTo(storage: EscrowStorage, recipient: address, queryId: uint64) {
    val transfer = createMessage({
        bounce: BounceMode.Only256BitsOfBody,
        dest: storage.nftItem,
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
    assert (in.senderAddress == storage.nftItem) throw ERR_BAD_ASSET;
    if (storage.state == STATE_TRANSFER_PENDING | storage.state == STATE_REFUND_PENDING) {
        // The NFT item rejected the transfer. Do not assume ownership or retry automatically.
        storage.state = STATE_RECONCILIATION_REQUIRED;
        storage.save();
    }
}

fun onInternalMessage(in: InMessage) {
    if (in.body.isEmpty()) {
        // Acton deployment wrappers send an empty initial message. The explicit
        // DeployEscrow message below performs one-time configuration validation.
        return;
    }
    val msg = lazy AllowedMessage.fromSlice(in.body);

    match (msg) {
        DeployEscrow => {
            val storage = lazy EscrowStorage.load();
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (!storage.initialized) throw ERR_BAD_STATE;
            assert (storage.state == STATE_CREATED) throw ERR_BAD_STATE;
            assert (storage.expectedAmount > 0) throw ERR_BAD_AMOUNT;
            assert (storage.deadline > blockchain.now()) throw ERR_EXPIRED;
            assert (storage.seller != storage.buyer) throw ERR_BAD_ASSET;
            assert (storage.arbitrator1 != storage.arbitrator2) throw ERR_BAD_DISPUTE;
            assert (storage.nftItem != storage.seller) throw ERR_BAD_ASSET;
            storage.initialized = true;
            storage.save();
        }

        NotificationForNewOwner => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.nftItem) throw ERR_BAD_ASSET;
            assert (storage.state == STATE_CREATED) throw ERR_BAD_STATE;
            assert (msg.oldOwnerAddress == storage.seller) throw ERR_UNAUTHORIZED;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            // The seller must forward enough TON for a later TEP-62 transfer.
            assert (in.valueCoins >= grams("0.12")) throw ERR_NOT_ENOUGH_GAS;
            storage.state = STATE_DEPOSITED;
            storage.save();
        }

        RecordStarsPayment => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            // Safe replay: same Telegram charge and amount is a no-op.
            if (storage.state == STATE_PAID) {
                assert (storage.paymentReferenceHash == msg.telegramChargeHash) throw ERR_BAD_AMOUNT;
                assert (storage.expectedAmount == msg.amount) throw ERR_BAD_AMOUNT;
                return;
            }
            assert (storage.state == STATE_DEPOSITED) throw ERR_BAD_STATE;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            assert (msg.amount == storage.expectedAmount) throw ERR_BAD_AMOUNT;
            assert (msg.telegramChargeHash != 0) throw ERR_BAD_PROOF;
            storage.paymentReferenceHash = msg.telegramChargeHash;
            storage.state = STATE_PAID;
            storage.save();
        }

        RequestNftRelease => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.buyer) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_PAID) throw ERR_BAD_STATE;
            assert (blockchain.now() < storage.deadline) throw ERR_EXPIRED;
            assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
            storage.transferQueryId = msg.queryId;
            storage.state = STATE_TRANSFER_PENDING;
            storage.save();
            sendNftTo(storage, storage.buyer, msg.queryId);
        }

        ConfirmNftDelivery => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_TRANSFER_PENDING) throw ERR_BAD_STATE;
            assert (msg.buyerOwnershipProofHash != 0) throw ERR_BAD_PROOF;
            storage.deliveryProofHash = msg.buyerOwnershipProofHash;
            storage.state = STATE_DELIVERED;
            storage.save();
        }

        MarkSellerSettled => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_DELIVERED) throw ERR_BAD_STATE;
            assert (msg.payoutReferenceHash != 0) throw ERR_BAD_PROOF;
            storage.settlementReferenceHash = msg.payoutReferenceHash;
            storage.state = STATE_SETTLED;
            storage.save();
        }

        RequestRefund => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert ((storage.state == STATE_DEPOSITED) | (storage.state == STATE_PAID)) throw ERR_BAD_STATE;
            if (storage.state == STATE_DEPOSITED) {
                assert (blockchain.now() >= storage.deadline) throw ERR_EXPIRED;
            } else {
                // For XTR this hash is recorded only after refundStarPayment succeeds.
                assert (msg.telegramRefundHash != 0) throw ERR_BAD_PROOF;
                storage.refundReferenceHash = msg.telegramRefundHash;
            }
            assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
            storage.state = STATE_REFUND_PENDING;
            storage.save();
            sendNftTo(storage, storage.seller, msg.queryId);
        }

        ConfirmNftReturned => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_REFUND_PENDING) throw ERR_BAD_STATE;
            assert (msg.sellerOwnershipProofHash != 0) throw ERR_BAD_PROOF;
            storage.returnProofHash = msg.sellerOwnershipProofHash;
            storage.state = STATE_REFUNDED;
            storage.save();
        }

        RaiseDispute => {
            var storage = lazy EscrowStorage.load();
            assert ((in.senderAddress == storage.buyer) | (in.senderAddress == storage.seller)) throw ERR_UNAUTHORIZED;
            assert (msg.reasonHash != 0) throw ERR_BAD_DISPUTE;
            storage.disputeReasonHash = msg.reasonHash;
            assert ((storage.state == STATE_DEPOSITED) |
                    (storage.state == STATE_PAID) |
                    (storage.state == STATE_TRANSFER_PENDING)) throw ERR_BAD_STATE;
            if (storage.state == STATE_TRANSFER_PENDING) {
                // A transfer may already have executed on the NFT item. Never guess ownership.
                storage.state = STATE_RECONCILIATION_REQUIRED;
            } else {
                storage.preDisputeState = storage.state;
                storage.disputeReasonHash = msg.reasonHash;
                storage.state = STATE_DISPUTED;
            }
            storage.save();
        }

        RecordStarsRefund => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (in.senderAddress == storage.paymentAuthority) throw ERR_UNAUTHORIZED;
            assert (storage.state == STATE_DISPUTED) throw ERR_BAD_STATE;
            assert (storage.preDisputeState == STATE_PAID) throw ERR_BAD_STATE;
            assert (msg.telegramRefundHash != 0) throw ERR_BAD_PROOF;
            if (storage.refundReferenceHash != 0) {
                assert (storage.refundReferenceHash == msg.telegramRefundHash) throw ERR_BAD_PROOF;
                return;
            }
            storage.refundReferenceHash = msg.telegramRefundHash;
            storage.save();
        }

        ApproveDisputeResolution => {
            var storage = lazy EscrowStorage.load();
            assert (storage.initialized) throw ERR_BAD_STATE;
            assert (storage.state == STATE_DISPUTED) throw ERR_BAD_STATE;
            assert ((in.senderAddress == storage.arbitrator1) | (in.senderAddress == storage.arbitrator2)) throw ERR_UNAUTHORIZED;
            assert ((msg.decision == 1) | (msg.decision == 2)) throw ERR_BAD_DISPUTE;
            assert (msg.resolutionHash != 0) throw ERR_BAD_DISPUTE;

            val approval = in.senderAddress == storage.arbitrator1 ? 1 : 2;
            if (storage.disputeApprovals == 0) {
                storage.disputeApprovals = approval;
                storage.resolutionDecision = msg.decision;
                storage.resolutionHash = msg.resolutionHash;
                storage.save();
                return;
            }

            // Repeated approval from the same arbitrator is idempotent.
            if (storage.disputeApprovals == approval) {
                assert (storage.resolutionDecision == msg.decision) throw ERR_BAD_DISPUTE;
                assert (storage.resolutionHash == msg.resolutionHash) throw ERR_BAD_DISPUTE;
                return;
            }

            assert ((storage.disputeApprovals == 1) | (storage.disputeApprovals == 2)) throw ERR_BAD_DISPUTE;
            assert (storage.resolutionDecision == msg.decision) throw ERR_BAD_DISPUTE;
            assert (storage.resolutionHash == msg.resolutionHash) throw ERR_BAD_DISPUTE;

            if (msg.decision == 1) {
                if (storage.preDisputeState == STATE_PAID) {
                    assert (storage.refundReferenceHash != 0) throw ERR_BAD_PROOF;
                }
                assert (contract.getOriginalBalance() >= grams("0.15")) throw ERR_NOT_ENOUGH_GAS;
                storage.state = STATE_REFUND_PENDING;
                storage.disputeApprovals = 3;
                storage.save();
                sendNftTo(storage, storage.seller, msg.queryId);
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
    state: uint8
    seller: address
    buyer: address
    nftItem: address
    expectedAmount: uint128
    deadline: uint64
    paymentReferenceHash: uint256
    refundReferenceHash: uint256
    deliveryProofHash: uint256
    returnProofHash: uint256
    settlementReferenceHash: uint256
    disputeApprovals: uint8
    resolutionDecision: uint8
    resolutionHash: uint256
}

get fun get_escrow_state(): EscrowStateReply {
    val storage = lazy EscrowStorage.load();
    return {
        dealId: storage.dealId,
        state: storage.state,
        seller: storage.seller,
        buyer: storage.buyer,
        nftItem: storage.nftItem,
        expectedAmount: storage.expectedAmount,
        deadline: storage.deadline,
        paymentReferenceHash: storage.paymentReferenceHash,
        refundReferenceHash: storage.refundReferenceHash,
        deliveryProofHash: storage.deliveryProofHash,
        returnProofHash: storage.returnProofHash,
        settlementReferenceHash: storage.settlementReferenceHash,
        disputeApprovals: storage.disputeApprovals,
        resolutionDecision: storage.resolutionDecision,
        resolutionHash: storage.resolutionHash,
    };
}
