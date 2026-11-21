package com.suibian.app.domain

import kotlin.math.exp
import kotlin.math.sqrt

/**
 * 贝叶斯画像更新 —— 技术文档 7.5。
 * 与服务端镜像 server/app/engine/profile.py 完全同构，
 * 共用 schema/testvectors/profile.json 的测试向量（两边误差 < 1e-6，13.1）。
 */
object BayesUpdater {
    const val MU0 = 0.0
    const val TAU0 = 0.2
    const val SIGMA = 0.15
    const val N_CAP = 30

    data class Factor(
        val mu: Double,
        val tau: Double,
        val n: Int,
        /** 乘性系数 = exp(mu)；画像页显示"你步行比导航慢 12%"。 */
        val factor: Double,
        /** 可信区间下界 = exp(mu − 1.96·tau) */
        val factorLo: Double,
        /** 可信区间上界 = exp(mu + 1.96·tau) */
        val factorHi: Double,
    )

    /**
     * 对 log(实际/预计) 的正态共轭更新，只用最近 [N_CAP] 条未被排除的观测。
     * 用户在画像页删观测/改数值/重置 = 重新跑一次 update，不需要撤销逻辑（7.5）。
     */
    fun update(obs: List<Double>): Factor {
        val recent = obs.filter { !it.isNaN() }.takeLast(N_CAP)
        val n = recent.size
        val tau2 = 1.0 / (1.0 / (TAU0 * TAU0) + n / (SIGMA * SIGMA))
        val mu = tau2 * (MU0 / (TAU0 * TAU0) + recent.sum() / (SIGMA * SIGMA))
        val tau = sqrt(tau2)
        return Factor(
            mu = mu,
            tau = tau,
            n = n,
            factor = exp(mu),
            factorLo = exp(mu - 1.96 * tau),
            factorHi = exp(mu + 1.96 * tau),
        )
    }

    /** |r| > log 3 的观测不直接入库，复盘时先问"这段是不是中途去办了别的事？"（7.5）。 */
    fun isOutlier(r: Double): Boolean = kotlin.math.abs(r) > kotlin.math.ln(3.0)
}
