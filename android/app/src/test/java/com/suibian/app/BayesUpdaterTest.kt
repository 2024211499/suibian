package com.suibian.app

import com.suibian.app.domain.BayesUpdater
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 画像更新测试 —— 与服务端共用 schema/testvectors/profile.json 的期望值（容差 1e-6）。
 * 向量更新时必须两边同时改（技术文档 13.1）。
 */
class BayesUpdaterTest {

    @Test
    fun emptyObsReturnsPrior() {
        val f = BayesUpdater.update(emptyList())
        assertEquals(0.0, f.mu, 1e-6)
        assertEquals(0.2, f.tau, 1e-6)
        assertEquals(0, f.n)
        assertEquals(1.0, f.factor, 1e-6)
    }

    @Test
    fun singleObs() {
        val f = BayesUpdater.update(listOf(0.1))
        assertEquals(0.064, f.mu, 1e-6)
        assertEquals(0.12, f.tau, 1e-6)
        assertEquals(1, f.n)
    }

    @Test
    fun twoObs() {
        val f = BayesUpdater.update(listOf(0.1, 0.3))
        assertEquals(0.156098, f.mu, 1e-6)
        assertEquals(0.093704, f.tau, 1e-6)
        assertEquals(2, f.n)
    }

    @Test
    fun capUsesLast30() {
        val obs = List(5) { 0.1 } + List(30) { 0.2 }
        val f = BayesUpdater.update(obs)
        assertEquals(30, f.n)
        assertEquals(0.196319, f.mu, 1e-6)
        assertEquals(0.0271329, f.tau, 1e-6)
    }

    @Test
    fun outlierThreshold() {
        assertTrue(BayesUpdater.isOutlier(1.2))
        assertFalse(BayesUpdater.isOutlier(0.2))
    }
}
