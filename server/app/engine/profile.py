"""贝叶斯画像更新 —— 技术文档 7.5 的 Python 镜像（线上权威实现在手机端 Kotlin，
两边共用 schema/testvectors/profile.json 的测试向量，误差 < 1e-9）。"""

from __future__ import annotations

import math

# 常数与 schema/testvectors/profile.json 的 constants 一致
MU0 = 0.0
TAU0 = 0.2
SIGMA = 0.15
N_CAP = 30


def bayes_update(obs: list[float]) -> dict:
    """对 log(实际/预计) 做正态共轭更新，只用最近 N_CAP 条。

    返回 {"mu", "tau", "n", "factor"}；factor = exp(mu) 是乘性系数，
    可信区间 = exp(mu ± 1.96·tau)，画像页显示"你步行比导航慢 12%（8%–16%）"。
    """
    recent = [x for x in obs if x is not None][-N_CAP:]
    n = len(recent)
    tau2 = 1.0 / (1.0 / (TAU0 * TAU0) + n / (SIGMA * SIGMA))
    mu = tau2 * (MU0 / (TAU0 * TAU0) + sum(recent) / (SIGMA * SIGMA))
    return {"mu": mu, "tau": math.sqrt(tau2), "n": n, "factor": math.exp(mu)}


def update_fixed(obs_minutes: list[float], default_min: float, prior_mu_min: float = 0.0, prior_tau_min: float = 5.0,
                 obs_sigma_min: float = 5.0) -> dict:
    """固定时长（出门准备、等车）的更新：r = 实际分钟 − 默认分钟，按正态共轭算（7.5）。

    返回的 mu 是"偏差"，实际估计值 = default_min + mu。
    """
    recent = [x for x in obs_minutes if x is not None][-N_CAP:]
    n = len(recent)
    tau2 = 1.0 / (1.0 / (prior_tau_min**2) + n / (obs_sigma_min**2))
    mu = tau2 * (prior_mu_min / (prior_tau_min**2) + sum(recent) / (obs_sigma_min**2))
    return {"mu": mu, "tau": math.sqrt(tau2), "n": n, "estimate_min": default_min + mu}


def is_outlier(r: float) -> bool:
    """|r| > log 3 的观测不直接入库，复盘时先问用户（7.5）。"""
    return abs(r) > math.log(3)
