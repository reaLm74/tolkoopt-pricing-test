import asyncio
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase

from starter import recommend_price, sample_fetch_offers


def offer(price, seller="PARTS", delivery_days=3):
    return {
        "seller": seller,
        "price": Decimal(price),
        "delivery_days": delivery_days,
    }


def source(*offers, status="success"):
    async def fetch_offers():
        return {"status": status, "offers": list(offers)}

    return fetch_offers


class RecommendPriceTests(IsolatedAsyncioTestCase):
    async def test_sample_price(self):
        result = await recommend_price(
            sample_fetch_offers, Decimal("4900.00"), Decimal("4500.00")
        )

        self.assertIsInstance(result["price"], Decimal)
        self.assertEqual(result["price"], Decimal("4728.00"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_selects_cheapest_eligible_competitor_and_rounds_down(self):
        result = await recommend_price(
            source(
                offer("7.00", seller="MyShOp"),
                offer("8.00", delivery_days=None),
                offer("9.00", delivery_days=7),
                offer("10.15", delivery_days=6),
                offer("11.00"),
            ),
            Decimal("10.50"),
            Decimal("9.00"),
        )

        self.assertEqual(result["price"], Decimal("9.99"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_incomplete_data_keeps_current_price_even_below_floor(self):
        for status in ("partial", "error"):
            with self.subTest(status=status):
                result = await recommend_price(
                    source(offer("100.00"), status=status),
                    Decimal("70.00"),
                    Decimal("90.00"),
                )

                self.assertEqual(result["price"], Decimal("70.00"))
                self.assertEqual(result["decision"], "HOLD")

    async def test_no_eligible_offers_keeps_current_price_even_below_floor(self):
        result = await recommend_price(
            source(offer("100.00", seller="MYSHOP"), offer("90.00", delivery_days=None)),
            Decimal("70.00"),
            Decimal("90.00"),
        )

        self.assertEqual(result["price"], Decimal("70.00"))
        self.assertEqual(result["decision"], "HOLD")

    async def test_source_exception_keeps_current_price(self):
        async def failing_source():
            raise RuntimeError("upstream error")

        result = await recommend_price(
            failing_source, Decimal("100.00"), Decimal("90.00")
        )

        self.assertEqual(result["price"], Decimal("100.00"))
        self.assertEqual(result["decision"], "HOLD")

    async def test_timeout_keeps_current_price_during_source_cleanup(self):
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()
        cleanup_finished = asyncio.Event()

        async def slow_source():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await allow_cleanup.wait()
                return {"status": "success", "offers": [offer("100.00")]}
            finally:
                cleanup_finished.set()

        calculation = asyncio.create_task(
            recommend_price(
                slow_source, Decimal("100.00"), Decimal("90.00"), timeout_s=0.02
            )
        )
        try:
            done, _ = await asyncio.wait({calculation}, timeout=0.5)
            self.assertIn(calculation, done)
            result = calculation.result()

            self.assertEqual(result["price"], Decimal("100.00"))
            self.assertEqual(result["decision"], "HOLD")
            await asyncio.wait_for(cleanup_started.wait(), timeout=0.5)
            self.assertFalse(cleanup_finished.is_set())
        finally:
            allow_cleanup.set()
            if not calculation.done():
                calculation.cancel()
            await asyncio.gather(calculation, return_exceptions=True)
            if cleanup_started.is_set():
                await asyncio.wait_for(cleanup_finished.wait(), timeout=0.5)

    async def test_exact_thirty_percent_applies_and_extra_cent_requires_review(self):
        cases = (
            ("70.00", "APPLY"),
            ("130.00", "APPLY"),
            ("69.99", "REVIEW"),
            ("130.01", "REVIEW"),
        )
        for floor, decision in cases:
            with self.subTest(floor=floor):
                result = await recommend_price(
                    source(offer("1.00")), Decimal("100.00"), Decimal(floor)
                )

                self.assertEqual(result["price"], Decimal(floor))
                self.assertEqual(result["decision"], decision)

    async def test_large_amount_preserves_cent_precision(self):
        competitor_price = "1" + "0" * 30 + ".01"
        result = await recommend_price(
            source(offer(competitor_price)), Decimal(competitor_price), Decimal("1.00")
        )

        self.assertEqual(result["price"], Decimal("985" + "0" * 27 + ".00"))
        self.assertEqual(result["decision"], "APPLY")
