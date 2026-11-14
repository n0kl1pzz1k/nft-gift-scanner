enum PaymentEscrowErrors {
    Unauthorized = 2101,
    BadState = 2102,
    BadAsset = 2103,
    BadAmount = 2104,
    Expired = 2105,
    BadProof = 2106,
    BadQuorum = 2107,
    NotEnoughGas = 2108,
}

const MAX_DEAL_LIFETIME = 2592000;

const V2_CREATED = 0;
const V2_DEPOSITED = 1;
const V2_PAYMENT_PENDING = 2;
const V2_PAID = 3;
const V2_TRANSFER_PENDING = 4;
const V2_DELIVERED = 5;
const V2_DISPUTED = 6;
const V2_RECONCILIATION_REQUIRED = 7;
const V2_DISPUTE_DECISION_APPROVED = 8;
const V2_PAYOUT_PENDING = 9;
const V2_SETTLED = 10;
const V2_DISPUTE_RESOLVED = 11;

const ASSET_TON = 0;
const ASSET_TEST_USDT_JETTON = 1;

struct PaymentEscrowParties {
    seller: address
    buyer: address
    nftItem: address
}

struct PaymentEscrowSignerSet {
    signer1: address
    signer2: address
    signer3: address
}

struct PaymentEscrowArbitratorSet {
    arbitrator1: address
    arbitrator2: address
    arbitrator3: address
}

struct PaymentEscrowAssetConfig {
    usdtJettonMaster: address
    escrowUsdtJettonWallet: address?
}

struct PaymentEscrowAuthorityConfig {
    platformConfigAuthority: address
    resolverMultisig: address
    signers: Cell<PaymentEscrowSignerSet>
    arbitrators: Cell<PaymentEscrowArbitratorSet>
    assets: Cell<PaymentEscrowAssetConfig>
}

struct PaymentEscrowEvidence {
    deliveryProofHash: uint256
    disputeResolutionHash: uint256
    payoutTxHash: uint256
}

struct PaymentEscrowStorage {
    dealId: uint256
    expectedAmount: uint128
    deadline: uint64
    releaseAfter: uint64
    assetKind: uint8
    initialized: bool
    state: uint8
    paymentQueryId: uint64
    transferQueryId: uint64
    paymentApprovals: uint8
    deliveryApprovals: uint8
    disputeApprovals: uint8
    disputeDecision: uint8
    payoutQueryId: uint64
    payoutApprovals: uint8
    evidence: Cell<PaymentEscrowEvidence>
    parties: Cell<PaymentEscrowParties>
    authorities: Cell<PaymentEscrowAuthorityConfig>
}

struct (0x5fcc3d14) AskToChangeOwnership {
    queryId: uint64
    newOwnerAddress: address
    sendExcessesTo: address?
    customPayload: cell?
    forwardGrams: coins
    forwardPayload: RemainingBitsAndRefs
}

struct (0x47525601) InitializePaymentEscrow {
    queryId: uint64
    escrowUsdtJettonWallet: address?
}

struct (0x05138d91) PaymentEscrowNftDeposit {
    queryId: uint64
    oldOwnerAddress: address
    payload: RemainingBitsAndRefs
}

struct (0x47525602) DepositTonPayment {
    queryId: uint64
    amount: uint128
}

struct (0x7362d09c) JettonTransferNotification {
    queryId: uint64
    amount: coins
    sender: address
    forwardPayload: RemainingBitsAndRefs
}

struct (0x47525603) ConfirmEscrowPayment {
    queryId: uint64
    amount: uint128
}

struct (0x47525604) RequestEscrowNftRelease {
    queryId: uint64
}

struct (0x47525605) ConfirmEscrowNftDelivery {
    queryId: uint64
    buyerOwnershipProofHash: uint256
}

struct (0x47525606) RaisePaymentEscrowDispute {
    queryId: uint64
    reasonHash: uint256
}

struct (0x47525607) ApprovePaymentEscrowDispute {
    queryId: uint64
    decision: uint8
    resolutionHash: uint256
}

struct (0x47525608) SellerPayoutNotice {
    queryId: uint64
    amount: uint128
}

struct (0x0f8a7ea5) PaymentEscrowJettonTransfer {
    queryId: uint64
    jettonAmount: coins
    transferRecipient: address
    sendExcessesTo: address?
    customPayload: cell?
    forwardTonAmount: coins
    forwardPayload: RemainingBitsAndRefs
}

struct (0x47525609) ConfirmEscrowSellerPayout {
    queryId: uint64
    payoutTxHash: uint256
}

type PaymentEscrowMessages =
    | InitializePaymentEscrow
    | PaymentEscrowNftDeposit
    | DepositTonPayment
    | JettonTransferNotification
    | ConfirmEscrowPayment
    | RequestEscrowNftRelease
    | ConfirmEscrowNftDelivery
    | RaisePaymentEscrowDispute
    | ApprovePaymentEscrowDispute
    | ConfirmEscrowSellerPayout

struct PaymentEscrowStateReply {
    dealId: uint256
    expectedAmount: uint128
    deadline: uint64
    releaseAfter: uint64
    assetKind: uint8
    initialized: bool
    state: uint8
    paymentQueryId: uint64
    transferQueryId: uint64
    paymentApprovals: uint8
    deliveryApprovals: uint8
    deliveryProofHash: uint256
    disputeApprovals: uint8
    disputeDecision: uint8
    disputeResolutionHash: uint256
    payoutQueryId: uint64
    payoutApprovals: uint8
    payoutTxHash: uint256
}
