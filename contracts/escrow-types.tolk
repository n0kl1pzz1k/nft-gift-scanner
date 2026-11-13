enum Errors {
    Unauthorized = 1001,
    BadState = 1002,
    BadAsset = 1003,
    BadAmount = 1004,
    Expired = 1005,
    BadProof = 1006,
    BadDispute = 1007,
    UnknownMessage = 1008,
    NotEnoughGas = 1009,
}

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
    resolutionEvidenceHash: uint256
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
    paymentEvidence: Cell<PaymentEvidence>
    deliveryEvidence: Cell<DeliveryEvidence>
}

struct (0x05138d91) NotificationForNewOwner {
    queryId: uint64
    oldOwnerAddress: address
    payload: RemainingBitsAndRefs
}

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

struct (0x4752440b) RecordNoPaymentEvidence {
    queryId: uint64
    reconciliationProofHash: uint256
}

struct (0x4752440a) ApproveDisputeResolution {
    queryId: uint64
    decision: uint8
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
    | RecordNoPaymentEvidence
    | ApproveDisputeResolution

struct EscrowStateReply {
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
}
