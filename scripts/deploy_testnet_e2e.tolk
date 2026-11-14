import "@acton/io"
import "@acton/emulation/network"
import "@acton/emulation/scripts"
import "@acton/testing/assert"

import "@contracts/payment-escrow-types"
import "@contracts/test-jetton/messages"
import "@contracts/test-jetton/storage"
import "@wrappers/GiftRadarPaymentEscrow.gen"
import "@wrappers/MockNftItem.gen"
import "@wrappers/MockNftCollection.gen"
import "@contracts/test-support/mock-nft-collection-types"
import "@wrappers/JettonMinter.gen"

fun buildTestNftContent(): cell {
    var metadata: OnchainMetadataReply = { contentDict: [] };
    metadata.contentDict.set("name".sha256(), SnakeDataReply { string: "GIFT RADAR Test NFT #0" }.toCell());
    metadata.contentDict.set("description".sha256(), SnakeDataReply { string: "Testnet-only TEP-62 NFT for escrow integration testing." }.toCell());
    metadata.contentDict.set("image".sha256(), SnakeDataReply { string: "https://example.com/gift-radar-testnet-nft.png" }.toCell());
    return metadata.toCell();
}

fun buildTestJettonMetadata(): cell {
    var metadata: OnchainMetadataReply = { contentDict: [] };
    metadata.contentDict.set("name".sha256(), SnakeDataReply { string: "GIFT RADAR Test USDT" }.toCell());
    metadata.contentDict.set("symbol".sha256(), SnakeDataReply { string: "tUSDT" }.toCell());
    metadata.contentDict.set("description".sha256(), SnakeDataReply { string: "Testnet-only token. Not issued by Tether." }.toCell());
    metadata.contentDict.set("decimals".sha256(), SnakeDataReply { string: "6" }.toCell());
    return metadata.toCell();
}

fun readState(escrow: GiftRadarPaymentEscrow): PaymentEscrowStateReply {
    return net.runGetMethod<PaymentEscrowStateReply>(
        escrow.address,
        "get_payment_escrow_state",
    );
}

fun main() {
    // Testnet demo only: wallets.toml maps distinct wallet IDs to one dedicated
    // test-only mnemonic. Never reuse this mnemonic for production signers.
    val deployer = scripts.wallet("deployer");
    val seller = scripts.wallet("seller");
    val buyer = scripts.wallet("buyer");
    val signer1 = scripts.wallet("signer1");
    val signer2 = scripts.wallet("signer2");
    val signer3 = scripts.wallet("signer3");
    val arb1 = scripts.wallet("arb1");
    val arb2 = scripts.wallet("arb2");
    val arb3 = scripts.wallet("arb3");
    val resolver = scripts.wallet("resolver");

    val amount: coins = 1000000; // 1.000000 tUSDT
    val dealId: uint256 = 0x4752414452414441520000000000000000000000000000000000000000000001;

    // Fund wallets that submit transactions in this isolated demo.
    val fundSeller = net.send(deployer.address, createMessage({
        bounce: false, value: grams("0.3"), dest: seller.address, body: createEmptySlice(),
    }));
    if (fundSeller.waitForTrace() == null) { Assert.fail("Could not fund seller test wallet"); }
    val fundBuyer = net.send(deployer.address, createMessage({
        bounce: false, value: grams("0.6"), dest: buyer.address, body: createEmptySlice(),
    }));
    if (fundBuyer.waitForTrace() == null) { Assert.fail("Could not fund buyer test wallet"); }
    val fundSigner1 = net.send(deployer.address, createMessage({
        bounce: false, value: grams("0.15"), dest: signer1.address, body: createEmptySlice(),
    }));
    if (fundSigner1.waitForTrace() == null) { Assert.fail("Could not fund signer 1"); }
    val fundSigner2 = net.send(deployer.address, createMessage({
        bounce: false, value: grams("0.15"), dest: signer2.address, body: createEmptySlice(),
    }));
    if (fundSigner2.waitForTrace() == null) { Assert.fail("Could not fund signer 2"); }

    // Deploy the platform-issued test Jetton. It is not real USDT.
    val minter = JettonMinter.fromStorage({
        totalSupply: 0,
        adminAddress: deployer.address,
        nextAdminAddress: null,
        metadata: buildTestJettonMetadata(),
    });
    val minterDeploy = net.send(deployer.address, createMessage({
        bounce: false,
        value: grams("1"),
        dest: { workchain: 0, stateInit: minter.stateInit! },
        body: TopUpTons {},
    }));
    if (minterDeploy.waitForTrace() == null) { Assert.fail("Test Jetton minter deployment failed"); }

    // Deploy a minimal TEP-62 collection and item for the testnet demo.
    val nftContent = buildTestNftContent();
    val collection = MockNftCollection.fromStorage({
        ownerAddress: seller.address,
        itemAddress: null,
        content: nftContent,
    });
    val collectionDeploy = collection.deploy(deployer.address, { value: grams("0.2") });
    if (collectionDeploy.waitForTrace() == null) { Assert.fail("Test NFT collection deployment failed"); }

    val nftItem = MockNftItem.fromStorage({
        ownerAddress: seller.address,
        collectionAddress: collection.address,
        index: 0,
        content: nftContent,
    });
    val nftDeploy = nftItem.deploy(deployer.address, { value: grams("0.2") });
    if (nftDeploy.waitForTrace() == null) { Assert.fail("Test NFT item deployment failed"); }
    val registerItem = net.send(seller.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: collection.address,
        body: MockNftCollectionSetItem { itemAddress: nftItem.address },
    }));
    if (registerItem.waitForTrace() == null) { Assert.fail("Test NFT item registration failed"); }

    // Deploy one per-deal payment escrow. Zero release delay is testnet-demo only.
    val escrow = GiftRadarPaymentEscrow.fromStorage({
        dealId,
        expectedAmount: amount,
        deadline: blockchain.now() + 2592000,
        releaseAfter: 0,
        releaseDelaySeconds: 0,
        assetKind: ASSET_TEST_USDT_JETTON,
        initialized: false,
        state: V2_CREATED,
        preDisputeState: V2_CREATED,
        paymentQueryId: 0,
        transferQueryId: 0,
        paymentApprovals: 0,
        deliveryApprovals: 0,
        disputeApprovals: 0,
        disputeDecision: 0,
        payoutQueryId: 0,
        payoutApprovals: 0,
        refundQueryId: 0,
        refundApprovals: 0,
        evidence: PaymentEscrowEvidence {
            deliveryProofHash: 0,
            disputeResolutionHash: 0,
            payoutTxHash: 0,
        }.toCell(),
        refundEvidence: PaymentEscrowRefundEvidence {
            refundTxHash: 0,
            sellerOwnershipProofHash: 0,
        }.toCell(),
        parties: PaymentEscrowParties {
            seller: seller.address,
            buyer: buyer.address,
            nftItem: nftItem.address,
        }.toCell(),
        authorities: PaymentEscrowAuthorityConfig {
            platformConfigAuthority: deployer.address,
            resolverMultisig: resolver.address,
            signers: PaymentEscrowSignerSet {
                signer1: signer1.address,
                signer2: signer2.address,
                signer3: signer3.address,
            }.toCell(),
            arbitrators: PaymentEscrowArbitratorSet {
                arbitrator1: arb1.address,
                arbitrator2: arb2.address,
                arbitrator3: arb3.address,
            }.toCell(),
            assets: PaymentEscrowAssetConfig {
                usdtJettonMaster: minter.address,
                escrowUsdtJettonWallet: null,
            }.toCell(),
        }.toCell(),
    });
    val escrowDeploy = escrow.deploy(deployer.address, { value: grams("0.5") });
    if (escrowDeploy.waitForTrace() == null) { Assert.fail("Payment escrow deployment failed"); }

    val escrowJettonWallet = minter.getWalletAddress(escrow.address);
    val buyerJettonWallet = minter.getWalletAddress(buyer.address);
    val sellerJettonWallet = minter.getWalletAddress(seller.address);
    val initialized = net.send(deployer.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: InitializePaymentEscrow {
            queryId: 1,
            escrowUsdtJettonWallet: escrowJettonWallet,
        },
    }));
    if (initialized.waitForTrace() == null) { Assert.fail("Payment escrow initialization failed"); }

    // Mint one test tUSDT to the buyer.
    val mintInternal = InternalTransferStep {
        queryId: 2,
        jettonAmount: amount,
        transferInitiator: null,
        sendExcessesTo: deployer.address,
        forwardTonAmount: 0,
        forwardPayload: beginCell().storeUint(0, 1).toSlice(),
    }.toCell();
    val mint = net.send(deployer.address, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: minter.address,
        body: MintNewJettons {
            queryId: 2,
            mintRecipient: buyer.address,
            tonAmount: grams("0.2"),
            internalTransferMsg: mintInternal,
        },
    }));
    if (mint.waitForTrace() == null) { Assert.fail("Test tUSDT mint failed"); }

    // Seller deposits the NFT into the payment escrow.
    val nftDeposit = net.send(seller.address, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: nftItem.address,
        body: AskToChangeOwnership {
            queryId: 3,
            newOwnerAddress: escrow.address,
            sendExcessesTo: seller.address,
            customPayload: null,
            forwardGrams: grams("0.15"),
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    }));
    if (nftDeposit.waitForTrace() == null) { Assert.fail("NFT deposit transaction failed"); }
    if (net.runGetMethod<address>(nftItem.address, "get_nft_owner") != escrow.address) {
        Assert.fail("NFT owner is not the escrow after deposit");
    }
    if (readState(escrow).state != V2_DEPOSITED) {
        Assert.fail("Payment escrow did not record the NFT deposit");
    }

    // Buyer transfers the standard TEP-74 test Jetton to escrow.
    val payment = net.send(buyer.address, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: buyerJettonWallet,
        body: AskToTransfer {
            queryId: 4,
            jettonAmount: amount,
            transferRecipient: escrow.address,
            sendExcessesTo: buyer.address,
            customPayload: null,
            forwardTonAmount: grams("0.05"),
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    }));
    if (payment.waitForTrace() == null) { Assert.fail("Buyer tUSDT payment failed"); }
    if (readState(escrow).state != V2_PAYMENT_PENDING) {
        Assert.fail("Escrow did not observe the tUSDT payment");
    }

    val paymentApproval1 = net.send(signer1.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowPayment { queryId: 4, amount },
    }));
    if (paymentApproval1.waitForTrace() == null) { Assert.fail("First payment approval failed"); }
    val paymentApproval2 = net.send(signer2.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowPayment { queryId: 4, amount },
    }));
    if (paymentApproval2.waitForTrace() == null) { Assert.fail("Second payment approval failed"); }
    if (readState(escrow).state != V2_PAID) {
        Assert.fail("2-of-3 payment approval did not advance escrow");
    }

    // The short release delay is deliberately limited to this testnet demo.
    val release = net.send(buyer.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: RequestEscrowNftRelease { queryId: 5 },
    }));
    if (release.waitForTrace() == null) { Assert.fail("NFT release to buyer failed"); }
    if (net.runGetMethod<address>(nftItem.address, "get_nft_owner") != buyer.address) {
        Assert.fail("NFT owner did not change to buyer");
    }

    val proof: uint256 = 0x4752414452414441524e465450524f4f46;
    val deliveryApproval1 = net.send(signer1.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 6, buyerOwnershipProofHash: proof },
    }));
    if (deliveryApproval1.waitForTrace() == null) { Assert.fail("First NFT delivery approval failed"); }
    val deliveryApproval2 = net.send(signer2.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 6, buyerOwnershipProofHash: proof },
    }));
    if (deliveryApproval2.waitForTrace() == null) { Assert.fail("Second NFT delivery approval failed"); }
    if (readState(escrow).state != V2_PAYOUT_PENDING) {
        Assert.fail("Seller payout was not triggered after delivery quorum");
    }

    val sellerJettonData = net.runGetMethod<JettonWalletDataReply>(sellerJettonWallet, "get_wallet_data");
    if (sellerJettonData.jettonBalance != amount) {
        Assert.fail("Seller did not receive the expected tUSDT payout");
    }

    // This is a test proof marker only. Production payout hashes must be
    // independently verified by two TON providers before signer confirmation.
    val payoutProof: uint256 = 0x4752414452414441525041594f5554;
    val payoutApproval1 = net.send(signer1.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 6, payoutTxHash: payoutProof },
    }));
    if (payoutApproval1.waitForTrace() == null) { Assert.fail("First payout approval failed"); }
    val payoutApproval2 = net.send(signer2.address, createMessage({
        bounce: true, value: grams("0.05"), dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 6, payoutTxHash: payoutProof },
    }));
    if (payoutApproval2.waitForTrace() == null) { Assert.fail("Second payout approval failed"); }
    if (readState(escrow).state != V2_SETTLED) {
        Assert.fail("Escrow did not settle after 2-of-3 payout confirmation");
    }

    println("TESTNET_E2E_STATUS=SETTLED");
    println("PAYMENT_ESCROW_ADDRESS={}", escrow.address);
    println("NFT_ITEM_ADDRESS={}", nftItem.address);
    println("NFT_COLLECTION_ADDRESS={}", collection.address);
    println("JETTON_MASTER_ADDRESS={}", minter.address);
    println("ESCROW_JETTON_WALLET={}", escrowJettonWallet);
    println("BUYER_JETTON_WALLET={}", buyerJettonWallet);
    println("SELLER_JETTON_WALLET={}", sellerJettonWallet);
    println("SELLER_ADDRESS={}", seller.address);
    println("BUYER_ADDRESS={}", buyer.address);
    println("SIGNER_1_ADDRESS={}", signer1.address);
    println("SIGNER_2_ADDRESS={}", signer2.address);
    println("DEAL_ID={}", dealId);
    println("AMOUNT_RAW={}", amount);
}
