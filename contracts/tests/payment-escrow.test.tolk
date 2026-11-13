import "@acton/emulation/network"
import "@acton/emulation/testing"
import "@acton/testing/expect"
import "@contracts/payment-escrow-types"
import "@wrappers/GiftRadarPaymentEscrow.gen"

fun setupPaymentEscrow(assetKind: uint8, amount: uint128): (GiftRadarPaymentEscrow, address, address, address, address, address, address, address, address) {
    val platformAdmin = testing.treasury("platform-config-admin");
    val seller = testing.treasury("payment-seller");
    val buyer = testing.treasury("payment-buyer");
    val nftItem = testing.treasury("payment-nft-item");
    val signer1 = testing.treasury("payment-signer-1");
    val signer2 = testing.treasury("payment-signer-2");
    val signer3 = testing.treasury("payment-signer-3");
    val arb1 = testing.treasury("payment-arb-1");
    val arb2 = testing.treasury("payment-arb-2");
    val arb3 = testing.treasury("payment-arb-3");
    val resolver = testing.treasury("neutral-resolver-multisig");
    val usdtMaster = testing.treasury("test-usdt-master");
    val usdtWallet = testing.treasury("escrow-test-usdt-wallet");

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
        deliveryProofHash: 0,
        parties: PaymentEscrowParties {
            seller: seller.address,
            buyer: buyer.address,
            nftItem: nftItem.address,
        }.toCell(),
        authorities: PaymentEscrowAuthorityConfig {
            platformConfigAuthority: platformAdmin.address,
            signer1: signer1.address,
            signer2: signer2.address,
            signer3: signer3.address,
            arbitrator1: arb1.address,
            arbitrator2: arb2.address,
            arbitrator3: arb3.address,
            resolverMultisig: resolver.address,
            usdtJettonMaster: usdtMaster.address,
            escrowUsdtJettonWallet: usdtWallet.address,
        }.toCell(),
    });
    val deployed = escrow.deploy(platformAdmin.address, { value: grams("1") });
    expect(deployed).toHaveSuccessfulDeploy({ to: escrow.address });
    val initialized = net.send(platformAdmin.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: InitializePaymentEscrow { queryId: 1 },
    }));
    expect(initialized).toHaveSuccessfulTx<InitializePaymentEscrow>({
        from: platformAdmin.address,
        to: escrow.address,
    });
    return (escrow, seller.address, buyer.address, nftItem.address,
        signer1.address, signer2.address, signer3.address,
        usdtWallet.address, platformAdmin.address);
}

fun depositPaymentNft(escrow: GiftRadarPaymentEscrow, seller: address, nftItem: address) {
    val result = net.send(nftItem, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: escrow.address,
        body: PaymentEscrowNftDeposit {
            queryId: 1,
            oldOwnerAddress: seller,
            payload: createEmptySlice(),
        },
    }));
    expect(result).toHaveSuccessfulTx<PaymentEscrowNftDeposit>({
        from: nftItem,
        to: escrow.address,
    });
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
    val (escrow, seller, buyer, nftItem, signer1, signer2, _, _, _platformAdmin) =
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
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DELIVERED);
    expect(readPaymentEscrowState(escrow).deliveryApprovals).toEqual(3);
}

get fun `test test USDT Jetton notification must come from configured escrow Jetton wallet and buyer`() {
    val (escrow, seller, buyer, nftItem, signer1, signer2, _, usdtWallet, _) =
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
            forwardPayload: createEmptySlice(),
        },
    }));
    expect(fake).toHaveFailedTx<JettonTransferNotification>({
        from: fakeWallet.address,
        to: escrow.address,
        exitCode: 2101,
    });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_DEPOSITED);

    val deposit = net.send(usdtWallet, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: JettonTransferNotification {
            queryId: 8002,
            amount: 1000000,
            sender: buyer,
            forwardPayload: createEmptySlice(),
        },
    }));
    expect(deposit).toHaveSuccessfulTx<JettonTransferNotification>({ from: usdtWallet, to: escrow.address });
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAYMENT_PENDING);
    confirmPaymentTwice(escrow, signer1, signer2, 8002, 1000000);
    expect(readPaymentEscrowState(escrow).state).toEqual(V2_PAID);
}
