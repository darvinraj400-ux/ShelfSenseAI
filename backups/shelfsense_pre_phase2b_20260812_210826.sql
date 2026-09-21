-- ShelfSenseAI backup before Phase 2B migration (2026-08-12T21:08:26.533482)

-- user: 9 rows
INSERT INTO user (id, email, password_hash, role, shop_id) VALUES
(1, 'admin123@gmail.com', 'scrypt:32768:8:1$J1tt5xNmTB2VzqPH$ff1f8adb3c4831fa13e683dff4ab49257b3588709abb1bfc052fbe061f74e79063409943f8d2963d71c0f8fb77bed7abb82cb10b0b859bc76ce7bb5aa86ad76e', 'staff', 2),
(2, 'testingacc@gmail.com', 'scrypt:32768:8:1$rNnj6EIsJQqxRiwe$f4083dc9e01ca0c5feb920ce7d2151ea381895865f410b633b5d0fd66ae9014fc2d43e931e7a8dcadd927f6c8495467a16463a9d0c063385e3a015ab774a3a29', 'staff', 3),
(3, 'testingacc20@gmail.com', 'scrypt:32768:8:1$GmQRh4sCHeSV5KkW$5fb38fe8ab98451e43245dcc854c5de210e1e3e18225370dc50b5ee159b21287b0fb21a50e8391c7c91dbb40bbc7aefb933d81da41bf1b9f438bb143b3a643b4', 'staff', 4),
(4, 'testacc30@gmail.com', 'scrypt:32768:8:1$r91cF7VpFWmgPze7$ee2c99a6243feb5148cb33f644a64db0023c2f60c44e5aacb31a17480867473f43b0a9038cd30d1a3f11e3fc92864bc3a38357939b93d7cecd5cc758eb499a01', 'staff', 5),
(5, 'owner@demo.my', 'scrypt:32768:8:1$iHtXaXt0BHLu5JcN$fcc2d156fed1c63afb8d9e857930f454000a87f75346d6f157d8de5c98f15fbc7047316d8f243e4db5f8dffcc247d86ed35390d0ba16e00caa341aa16b8c9cab', 'owner', 1),
(6, 'manager@demo.my', 'scrypt:32768:8:1$Uh1SpmmSQbAUDB9H$aca9eb23f42038d8ade56953708f372382948b07278fd32810de53974e5f9a15e5adc528cb18796d63e8c247872af0f642c881164716a6268dea520fbf15c8fa', 'manager', 1),
(7, 'staff@demo.my', 'scrypt:32768:8:1$yvCtdyAeq45tStmk$6f81c5ce5206b9b50d2f9fb6dd87703d116fd4c41d06c267094c73f876f56fd5b06155524324f9db2e8281eda2783740dd84011b44b53e255f9c10328b0bb4ce', 'staff', 1),
(8, 'newstaff@demo.my', 'scrypt:32768:8:1$O6WOAOny3ykuOj82$d4cf444f692d07b95fc323ca589b7e90bac2f35ae4fd2cb1ca8088560c5c1c8e0e1c30e4d411beec37826538eb2de49d904e286187d44bbd39d9966577cea454', 'staff', 6),
(9, 'owner123@email.com', 'scrypt:32768:8:1$pr2RtELZHkoQpVos$7e60e642c3b24fee67834b684d7b718a451e9aa567781b067d40f6290178481afa2490e3b352ee9e2163775219ce179fc75d5e782aff909a8b03a55c5ec19b8e', 'owner', 7);

-- shop: 7 rows
INSERT INTO shop (id, name, created_at) VALUES
(1, 'Demo Retail Shop', '2026-08-11 13:09:56'),
(2, 'Shop of admin123@gmail.com', '2026-08-11 13:09:56'),
(3, 'Shop of testingacc@gmail.com', '2026-08-11 13:09:56'),
(4, 'Shop of testingacc20@gmail.com', '2026-08-11 13:09:56'),
(5, 'Shop of testacc30@gmail.com', '2026-08-11 13:09:56'),
(6, 'Shop of newstaff@demo.my', '2026-08-11 13:09:56'),
(7, 'Shop of owner123@email.com', '2026-08-11 13:09:56');

-- product: 10 rows
INSERT INTO product (id, name, cost_price, target_margin, category, baseline_margin, shop_id, brand, quantity, unit, selling_price) VALUES
(5, 'TIGER BISKUAT SUSU', 5.0, 30.0, 'BISKUT', NULL, 5, NULL, NULL, NULL, NULL),
(6, 'LADA BENGGALA HIJAU (CAPSICUM)', 4.0, 20.0, 'SAYUR-SAYURAN', NULL, 5, NULL, NULL, NULL, NULL),
(7, 'BERAS CAP JASMINE (SST5%)', 23.5, 12.0, 'BERAS', 12.0, 1, NULL, NULL, NULL, NULL),
(8, 'TELUR AYAM GRED A', 12.0, 18.0, 'TELUR', 18.0, 1, NULL, NULL, NULL, NULL),
(9, 'SUSU TEPUNG SEGERA DUTCHLADY (BIASA )', 16.5, 22.0, 'KRIMER DAN SUSU TEPUNG', 22.0, 1, NULL, NULL, NULL, NULL),
(10, 'GULA PUTIH BERTAPIS HALUS (PELBAGAI JENAMA)', 2.6, 15.0, 'GULA', 15.0, 1, NULL, NULL, NULL, NULL),
(11, 'SUSU SEGAR KURMA FARM FRESH', 7.9, 20.0, 'TERSEDIA MINUM', 20.0, 1, NULL, NULL, NULL, NULL),
(12, 'KOPI TARIK PREMIUM', 4.0, 40.0, 'MINUMAN', 25.0, 1, NULL, NULL, NULL, NULL),
(13, 'MILO ''O'' PANAS', 2.2, 30.0, 'MINUMAN', 30.0, 1, NULL, NULL, NULL, NULL),
(17, 'SUSTAGEN KID 3+ (VANILLA) - KOTAK', 15.0, 30.0, 'SUSU BAYI', 30.0, 7, NULL, NULL, NULL, NULL);

-- price_history: 18 rows
INSERT INTO price_history (id, product_id, cost_price, target_margin, created_at, selling_price) VALUES
(1, 7, 23.0, 12.0, '2026-06-28 00:00:00', NULL),
(2, 7, 22.5, 12.0, '2026-05-29 00:00:00', NULL),
(3, 8, 11.5, 18.0, '2026-07-08 00:00:00', NULL),
(4, 8, 12.0, 18.0, '2026-07-23 00:00:00', NULL),
(5, 9, 16.5, 20.0, '2026-07-13 00:00:00', NULL),
(6, 10, 2.55, 15.0, '2026-07-18 00:00:00', NULL),
(7, 12, 3.5, 25.0, '2026-08-07 13:24:20', NULL),
(8, 12, 3.5, 40.0, '2026-08-07 13:24:20', NULL),
(9, 12, 4.0, 40.0, '2026-08-07 13:24:20', NULL),
(10, 13, 2.2, 30.0, '2026-08-07 13:26:02', NULL),
(11, 13, 2.2, 50.0, '2026-08-07 13:26:35', NULL),
(16, 17, 15.0, 30.0, '2026-08-08 04:11:24', NULL),
(17, 7, 23.0, 12.0, '2026-07-02 00:00:00', NULL),
(18, 7, 22.5, 12.0, '2026-06-02 00:00:00', NULL),
(19, 8, 11.5, 18.0, '2026-07-12 00:00:00', NULL),
(20, 8, 12.0, 18.0, '2026-07-27 00:00:00', NULL),
(21, 9, 16.5, 20.0, '2026-07-17 00:00:00', NULL),
(22, 10, 2.55, 15.0, '2026-07-22 00:00:00', NULL);