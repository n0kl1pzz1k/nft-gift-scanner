import "@acton/emulation/network"
import "@acton/emulation/testing"
import "@acton/testing/expect"
import "@contracts/payment-escrow-types"
import "@wrappers/GiftRadarPaymentEscrow.gen"
import "@wrappers/MockNftItem.gen"
import "@wrappers/JettonMinter.gen"
import "@contracts/test-jetton/messages"
import "@contracts/test-jetton/storage"

fun buildTestJettonMetadata(): cell {
    var metadata: OnchainMetadataReply = { contentDict: [] };
    metadata.contentDict.set("name".sha256(), SnakeDataReply { string: "GIFT RADAR Test USDT" }.toCell());
    metadata.contentDict.set("symbol".sha256(), SnakeDataReply { string: "tUSDT" }.toCell());
    metadata.contentDict.set("description".sha256(), SnakeDataReply { string: "Testnet-only token. Not issued by Tether." }.toCell());
    metadata.contentDict.set("decimals".sha256(), SnakeDataReply { string: "6" }.toCell());
    return metadata.toCell();
}

fun setupPaymentEscrow(assetKind: uint8, amount: uint128): (GiftRadarPaymentEscrow, address, address, address, address, address, address, address, address, address, address) {
    val platformAdmin = testing.treasury("platform-config-admin");
    val seller = testing.treasury("payment-seller");
    val buyer = testing.treasury("payment-buyer");
    val nftItem = MockNftItem.fromStorage({ ownerAddress: seller.address });
    val nftDeploy = nftItem.deploy(seller.address, { value: grams("1") });
    expect(nftDeploy).toHaveSuccessfulDeploy({ to: nftItem.address });
    val signer1 = testing.treasury("payment-signer-1");
    val signer2 = testing.treasury("payment-signer-2");
    val signer3 = testing.treasury("payment-signer-3");
    val arb1 = testing.treasury("payment-arb-1");
    val arb2 = testing.treasury("payment-arb-2");
    val arb3 = testing.treasury("payment-arb-3");
    val resolver = testing.treasury("neutral-resolver-multisig");
    val minter = JettonMinter.fromStorage({
        totalSupply: 0,
        adminAddress: platformAdmin.address,
        nextAdminAddress: null,
        metadata: buildTestJettonMetadata(),
    });
    val minterDeploy = net.send(platformAdmin.address, createMessage({
        bounce: false,
        value: grams("1"),
        dest: { workchain: 0, stateInit: minter.stateInit! },
        body: TopUpTons {},
    }));
    expect(minterDeploy).toHaveSuccessfulDeploy({ to: minter.address });

    val escrow = GiftRadarPaymentEscrow.fromStorage({
        dealId: 0x123456,
        expectedAmount: amount,
        deadline: testing.getNow() + 2592000,
        releaseAfter: 0,
        assetKind,
        initialized: false,
        state: V2_CREATED,
        paymentQueryId: 0,
        transferQueryId: 0,
        paymentApprovals: 0,
        deliveryApprovals: 0,
        disputeApprovals: 0,
        disputeDecision: 0,
        payoutQueryId: 0,
        payoutApprovals: 0,
        evidence: PaymentEscrowEvidence {
            deliveryProofHash: 0,
            disputeResolutionHash: 0,
            payoutTxHash: 0,
        }.toCell(),
        parties: PaymentEscrowParties {
            seller: seller.address,
            buyer: buyer.address,
            nftItem: nftItem.address,
        }.toCell(),
        authorities: PaymentEscrowAuthorityConfig {
            platformConfigAuthority: platformAdmin.address,
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
    val deployed = escrow.deploy(platformAdmin.address, { value: grams("1") });
    expect(deployed).toHaveSuccessfulDeploy({ to: escrow.address });
    val escrowUsdtWallet = minter.getWalletAddress(escrow.address);
    val buyerUsdtWallet = minter.getWalletAddress(buyer.address);
    val initialized = net.send(platformAdmin.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: InitializePaymentEscrow {
            queryId: 1,
            escrowUsdtJettonWallet: assetKind == ASSET_TEST_USDT_JETTON ? escrowUsdtWallet : null,
        },
    }));
    expect(initialized).toHaveSuccessfulTx<InitializePaymentEscrow>({
        from: platformAdmin.address,
        to: escrow.address,
    });
    val mintInternal = InternalTransferStep {
        queryId: 9001,
        jettonAmount: 1000000,
        transferInitiator: null,
        sendExcessesTo: platformAdmin.address,
        forwardTonAmount: 0,
        forwardPayload: beginCell().storeUint(0, 1).toSlice(),
    }.toCell();
    val mint = net.send(platformAdmin.address, createMessage({
        bounce: true,
        value: grams("1"),
        dest: minter.address,
        body: MintNewJettons {
            queryId: 9001,
            mintRecipient: buyer.address,
            tonAmount: grams("0.5"),
            internalTransferMsg: mintInternal,
        },
    }));
    expect(mint).toHaveSuccessfulTx<MintNewJettons>({ from: platformAdmin.address, to: minter.address });
    return (escrow, seller.address, buyer.address, nftItem.address,
        signer1.address, signer2.address, signer3.address,
        escrowUsdtWallet, buyerUsdtWallet, platformAdmin.address, minter.getWalletAddress(seller.address));
}

fun depositPaymentNft(escrow: GiftRadarPaymentEscrow, seller: address, nftItem: address) {
    val result = net.send(seller, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: nftItem,
        body: AskToChangeOwnership {
            queryId: 1,
            newOwnerAddress: escrow.address,
            sendExcessesTo: seller,
            customPayload: null,
            forwardGrams: grams("0.15"),
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    }));
    expect(result).toHaveSuccessfulTx<AskToChangeOwnership>({ from: seller, to: nftItem });
    expect(net.runGetMethod<address>(nftItem, "get_nft_owner")).toEqual(escrow.address);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DEPOSITED);
}

fun readPaymentEscrowState(escrow: GiftRadarPaymentEscrow): PaymentEscrowStateReply {
    return net.runGetMethod<PaymentEscrowStateReply>(
        escrow.address,
        "get_payment_escrow_state",
    );
}

fun confirmPaymentTwice(escrow: GiftRadarPaymentEscrow, signer1: address, signer2: address, queryId: uint64, amount: uint128) {
    val first = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowPayment { queryId, amount },
    }));
    expect(first).toHaveSuccessfulTx<ConfirmEscrowPayment>({ from: signer1, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYMENT_PENDING);
    expect(readPaymentEscrowState(escrow).paymentApprovals).toEqual(1);

    val duplicate = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowPayment { queryId, amount },
    }));
    expect(duplicate).toHaveSuccessfulTx<ConfirmEscrowPayment>({ from: signer1, to: escrow.address });
    expect(readPaymentEscrowState(escrow).paymentApprovals).toEqual(1);

    val second = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowPayment { queryId, amount },
    }));
    expect(second).toHaveSuccessfulTx<ConfirmEscrowPayment>({ from: signer2, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAID);
}

get fun `test TON payment enters escrow then requires two distinct signers before NFT release`() {
    val (escrow, seller, buyer, nftItem, signer1, signer2, _, _, _, _platformAdmin, _) =
        setupPaymentEscrow(ASSET_TON, 250000000);
    depositPaymentNft(escrow, seller, nftItem);

    val payment = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.30"),
        dest: escrow.address,
        body: DepositTonPayment { queryId: 7001, amount: 250000000 },
    }));
    expect(payment).toHaveSuccessfulTx<DepositTonPayment>({ from: buyer, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYMENT_PENDING);

    val outsider = testing.treasury("payment-outsider");
    val badApproval = net.send(outsider.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowPayment { queryId: 7001, amount: 250000000 },
    }));
    expect(badApproval).toHaveFailedTx<ConfirmEscrowPayment>({
        from: outsider.address,
        to: escrow.address,
        exitCode: 2101,
    });

    confirmPaymentTwice(escrow, signer1, signer2, 7001, 250000000);
    expect(readPaymentEscrowState(escrow).releaseAfter).toEqual(testing.getNow() + 86400);

    val tooEarly = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestEscrowNftRelease { queryId: 7002 },
    }));
    expect(tooEarly).toHaveFailedTx<RequestEscrowNftRelease>({
        from: buyer,
        to: escrow.address,
        exitCode: 2105,
    });

    testing.setNow(testing.getNow() + 86401);
    val release = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestEscrowNftRelease { queryId: 7003 },
    }));
    expect(release).toHaveSuccessfulTx<RequestEscrowNftRelease>({ from: buyer, to: escrow.address });
    expect(release).toHaveTx<AskToChangeOwnership>({ from: escrow.address, to: nftItem });
    expect(net.runGetMethod<address>(nftItem, "get_nft_owner")).toEqual(buyer);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_TRANSFER_PENDING);

    val proof = 0x987654321;
    val firstDelivery = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 7004, buyerOwnershipProofHash: proof },
    }));
    expect(firstDelivery).toHaveSuccessfulTx<ConfirmEscrowNftDelivery>({ from: signer1, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_TRANSFER_PENDING);

    val secondDelivery = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 7005, buyerOwnershipProofHash: proof },
    }));
    expect(secondDelivery).toHaveSuccessfulTx<ConfirmEscrowNftDelivery>({ from: signer2, to: escrow.address });
    expect(secondDelivery).toHaveSuccessfulTx<SellerPayoutNotice>({ from: escrow.address, to: seller });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYOUT_PENDING);
    expect(readPaymentEscrowState(escrow).deliveryApprovals).toEqual(3);
    expect(readPaymentEscrowState(escrow).payoutQueryId).toEqual(7005);
    val payoutConfirmation1 = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 7005, payoutTxHash: 0x7005abc },
    }));
    expect(payoutConfirmation1).toHaveSuccessfulTx<ConfirmEscrowSellerPayout>({ from: signer1, to: escrow.address });
    expect(readPaymentEscrowState(escrow).payoutApprovals).toEqual(1);
    val duplicatePayoutVote = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 7005, payoutTxHash: 0x7005abc },
    }));
    expect(duplicatePayoutVote).toHaveSuccessfulTx<ConfirmEscrowSellerPayout>({ from: signer1, to: escrow.address });
    expect(readPaymentEscrowState(escrow).payoutApprovals).toEqual(1);
    val payoutOutsider = testing.treasury("payout-outsider");
    val unauthorizedPayoutVote = net.send(payoutOutsider.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 7005, payoutTxHash: 0x7005abc },
    }));
    expect(unauthorizedPayoutVote).toHaveFailedTx<ConfirmEscrowSellerPayout>({
        from: payoutOutsider.address, to: escrow.address, exitCode: 2101,
    });
    val conflictingPayout = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 7005, payoutTxHash: 0x7005bad },
    }));
    expect(conflictingPayout).toHaveFailedTx<ConfirmEscrowSellerPayout>({
        from: signer2, to: escrow.address, exitCode: 2106,
    });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYOUT_PENDING);
    val payoutConfirmation2 = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 7005, payoutTxHash: 0x7005abc },
    }));
    expect(payoutConfirmation2).toHaveSuccessfulTx<ConfirmEscrowSellerPayout>({ from: signer2, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_SETTLED);
    expect(readPaymentEscrowState(escrow).payoutApprovals).toEqual(3);
}

get fun `test standard TEP-74 test Jetton transfers into escrow and needs two signers`() {
    val (escrow, seller, buyer, nftItem, signer1, signer2, _, usdtWallet, buyerUsdtWallet, _, sellerUsdtWallet) =
        setupPaymentEscrow(ASSET_TEST_USDT_JETTON, 1000000);
    depositPaymentNft(escrow, seller, nftItem);

    val fakeWallet = testing.treasury("fake-usdt-wallet");
    val fake = net.send(fakeWallet.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: JettonTransferNotification {
            queryId: 8001,
            amount: 1000000,
            sender: buyer,
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    }));
    expect(fake).toHaveFailedTx<JettonTransferNotification>({
        from: fakeWallet.address,
        to: escrow.address,
        exitCode: 2101,
    });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DEPOSITED);

    val transfer = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.5"),
        dest: buyerUsdtWallet,
        body: AskToTransfer {
            queryId: 8002,
            jettonAmount: 1000000,
            transferRecipient: escrow.address,
            sendExcessesTo: buyer,
            customPayload: null,
            forwardTonAmount: grams("0.05"),
            forwardPayload: beginCell().storeUint(0, 1).toSlice(),
        },
    }));
    expect(transfer).toHaveSuccessfulTx<AskToTransfer>({ from: buyer, to: buyerUsdtWallet });
    val buyerWalletData = net.runGetMethod<JettonWalletDataReply>(buyerUsdtWallet, "get_wallet_data");
    val escrowWalletData = net.runGetMethod<JettonWalletDataReply>(usdtWallet, "get_wallet_data");
    expect(buyerWalletData.jettonBalance).toEqual(0);
    expect(escrowWalletData.jettonBalance).toEqual(1000000);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYMENT_PENDING);

    confirmPaymentTwice(escrow, signer1, signer2, 8002, 1000000);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAID);

    testing.setNow(testing.getNow() + 86401);
    val release = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestEscrowNftRelease { queryId: 8003 },
    }));
    expect(release).toHaveSuccessfulTx<RequestEscrowNftRelease>({ from: buyer, to: escrow.address });
    expect(net.runGetMethod<address>(nftItem, "get_nft_owner")).toEqual(buyer);

    val proof = 0x123456789;
    val firstDelivery = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 8004, buyerOwnershipProofHash: proof },
    }));
    expect(firstDelivery).toHaveSuccessfulTx<ConfirmEscrowNftDelivery>({ from: signer1, to: escrow.address });
    val secondDelivery = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowNftDelivery { queryId: 8005, buyerOwnershipProofHash: proof },
    }));
    expect(secondDelivery).toHaveSuccessfulTx<ConfirmEscrowNftDelivery>({ from: signer2, to: escrow.address });
    expect(secondDelivery).toHaveTx<PaymentEscrowJettonTransfer>({ from: escrow.address, to: usdtWallet });
    val sellerWalletData = net.runGetMethod<JettonWalletDataReply>(sellerUsdtWallet, "get_wallet_data");
    expect(sellerWalletData.jettonBalance).toEqual(1000000);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYOUT_PENDING);
    expect(readPaymentEscrowState(escrow).payoutQueryId).toEqual(8005);
    val payoutConfirmation1 = net.send(signer1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 8005, payoutTxHash: 0x8005abc },
    }));
    expect(payoutConfirmation1).toHaveSuccessfulTx<ConfirmEscrowSellerPayout>({ from: signer1, to: escrow.address });
    val payoutConfirmation2 = net.send(signer2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmEscrowSellerPayout { queryId: 8005, payoutTxHash: 0x8005abc },
    }));
    expect(payoutConfirmation2).toHaveSuccessfulTx<ConfirmEscrowSellerPayout>({ from: signer2, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_SETTLED);
}


get fun `test dispute decision requires two distinct arbitrators and matching resolution evidence`() {
    val (escrow, seller, buyer, nftItem, _, _, _, _, _, _, _) =
        setupPaymentEscrow(ASSET_TON, 250000000);
    depositPaymentNft(escrow, seller, nftItem);

    val raised = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaisePaymentEscrowDispute { queryId: 9101, reasonHash: 0xabc123 },
    }));
    expect(raised).toHaveSuccessfulTx<RaisePaymentEscrowDispute>({ from: buyer, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DISPUTED);

    val arb1 = testing.treasury("payment-arb-1");
    val arb2 = testing.treasury("payment-arb-2");
    val arb3 = testing.treasury("payment-arb-3");
    val first = net.send(arb1.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApprovePaymentEscrowDispute {
            queryId: 9102, decision: 1, resolutionHash: 0xfeed01,
        },
    }));
    expect(first).toHaveSuccessfulTx<ApprovePaymentEscrowDispute>({ from: arb1.address, to: escrow.address });
    expect(readPaymentEscrowState(escrow).disputeApprovals).toEqual(1);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DISPUTED);

    val duplicate = net.send(arb1.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApprovePaymentEscrowDispute {
            queryId: 9103, decision: 1, resolutionHash: 0xfeed01,
        },
    }));
    expect(duplicate).toHaveSuccessfulTx<ApprovePaymentEscrowDispute>({ from: arb1.address, to: escrow.address });
    expect(readPaymentEscrowState(escrow).disputeApprovals).toEqual(1);

    val conflicting = net.send(arb2.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApprovePaymentEscrowDispute {
            queryId: 9104, decision: 2, resolutionHash: 0xfeed02,
        },
    }));
    expect(conflicting).toHaveFailedTx<ApprovePaymentEscrowDispute>({
        from: arb2.address, to: escrow.address, exitCode: 2106,
    });

    val second = net.send(arb2.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApprovePaymentEscrowDispute {
            queryId: 9105, decision: 1, resolutionHash: 0xfeed01,
        },
    }));
    expect(second).toHaveSuccessfulTx<ApprovePaymentEscrowDispute>({ from: arb2.address, to: escrow.address });
    expect(readPaymentEscrowState(escrow).disputeApprovals).toEqual(3);
    expect(readPaymentEscrowState(escrow).disputeDecision).toEqual(1);
    expect(readPaymentEscrowState(escrow).disputeResolutionHash).toEqual(0xfeed01);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DISPUTE_DECISION_APPROVED);

    val lateVote = net.send(arb3.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApprovePaymentEscrowDispute {
            queryId: 9106, decision: 1, resolutionHash: 0xfeed01,
        },
    }));
    expect(lateVote).toHaveFailedTx<ApprovePaymentEscrowDispute>({
        from: arb3.address, to: escrow.address, exitCode: 2102,
    });
}
