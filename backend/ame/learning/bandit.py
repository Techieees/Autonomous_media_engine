from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ame.analytics.classify import percentile
from ame.learning.features import LearningRecord, LearningTargets

MIN_ARM_N = 10
UCB_C = math.sqrt(2.0)
EPSILON = 0.15


@dataclass
class ArmStats:
    key: str
    value: str
    n: int
    mean: float
    median: float
    ucb: float | None
    thompson: float | None
    policy: str
    rewards: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "n": self.n,
            "mean": round(self.mean, 4),
            "median": round(self.median, 4),
            "ucb": None if self.ucb is None else round(self.ucb, 4),
            "thompson": None if self.thompson is None else round(self.thompson, 4),
            "policy": self.policy,
        }


def composite_reward(targets: LearningTargets) -> float:
    views = targets.views_24h or targets.views_6h or targets.views_1h or 0
    view_score = min(1.0, math.log1p(views) / math.log1p(20000))
    completion = min(1.0, max(0.0, targets.completion or 0.0))
    likes = targets.likes or 0
    comments = targets.comments or 0
    shares = targets.shares or 0
    followers = targets.followers or 0
    revenue = targets.revenue or 0.0
    like_rate = likes / views if views else 0.0
    share_rate = shares / views if views else 0.0
    comment_rate = comments / views if views else 0.0
    follower_rate = followers / views if views else 0.0
    return (
        0.30 * view_score
        + 0.20 * completion
        + 0.15 * min(1.0, share_rate * 50.0)
        + 0.10 * min(1.0, like_rate * 20.0)
        + 0.10 * min(1.0, comment_rate * 100.0)
        + 0.10 * min(1.0, follower_rate * 100.0)
        + 0.05 * min(1.0, float(revenue) / 10.0)
    )


def summarize_arms(
    records: list[LearningRecord],
    dimension: str,
    *,
    rng: random.Random | None = None,
) -> list[ArmStats]:
    rng = rng or random.Random(7)
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = str(getattr(record.features, dimension))
        grouped[value].append(composite_reward(record.targets))
    total_n = sum(len(items) for items in grouped.values())
    arms: list[ArmStats] = []
    for value, rewards in grouped.items():
        n = len(rewards)
        mean = sum(rewards) / n
        median = float(percentile(rewards, 50) or 0.0)
        if n >= MIN_ARM_N:
            ucb = mean + UCB_C * math.sqrt(math.log(max(total_n, 2)) / n)
            variance = statistics.pvariance(rewards) if n > 1 else 0.25
            sigma = math.sqrt(variance / n) if variance > 0 else 0.05
            thompson = rng.gauss(mean, sigma)
            policy = "ucb"
        else:
            ucb = None
            thompson = None
            policy = "exploratory"
        arms.append(
            ArmStats(
                key=dimension,
                value=value,
                n=n,
                mean=mean,
                median=median,
                ucb=ucb,
                thompson=thompson,
                policy=policy,
                rewards=rewards,
            )
        )
    arms.sort(key=lambda item: item.median, reverse=True)
    return arms


def choose_arm(
    arms: list[ArmStats],
    *,
    rng: random.Random | None = None,
) -> tuple[ArmStats | None, str]:
    if not arms:
        return None, "exploratory"
    rng = rng or random.Random(11)
    exploitable = [arm for arm in arms if arm.n >= MIN_ARM_N and arm.ucb is not None]
    exploratory = [arm for arm in arms if arm.n < MIN_ARM_N]
    if not exploitable:
        chosen = min(exploratory, key=lambda arm: arm.n)
        return chosen, "exploratory"
    if rng.random() < EPSILON:
        return rng.choice(exploitable), "epsilon_greedy"
    if rng.random() < 0.35:
        return max(exploitable, key=lambda arm: arm.thompson or arm.mean), "thompson"
    return max(exploitable, key=lambda arm: arm.ucb or arm.mean), "ucb"
