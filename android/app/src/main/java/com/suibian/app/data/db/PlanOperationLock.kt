package com.suibian.app.data.db

import kotlinx.coroutines.sync.Mutex
import javax.inject.Inject
import javax.inject.Singleton

/** Orders version switches with deployment, undo and notification dispatch within the app process. */
@Singleton
class PlanOperationLock @Inject constructor() {
    private val locks = Array(32) { Mutex() }

    internal fun forPlan(planId: String): Mutex = locks[Math.floorMod(planId.hashCode(), locks.size)]
}
