package com.suibian.app.data.db

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.Upsert
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/**
 * Room 表（技术文档 2.5）。
 * v2 变更（审计收紧-1）：
 *  - plan 改复合主键 (planId, version)：新版本插入不覆盖旧版本，isCurrent 标记当前版本，
 *    支持回退和"重复确认"审计；
 *  - 新增 action_log：动作关卡先写日志再执行（12.2 第 5 步），进程中断后可恢复；
 *  - 新增 alarm：闹钟持久化，开机/改时间后据此重建（10.3）。
 * 还缺的表：observation / profile_item / grant / choice / metrics（W2–W4 按需补）。
 */

@Entity(tableName = "plan", primaryKeys = ["planId", "version"])
data class PlanEntity(
    val planId: String,
    val version: Int,
    val status: String,
    /** 整张 PlanGraph 存 JSON */
    val json: String,
    val updatedAt: Long,
    /** 当前生效版本，由 saveCurrent 在事务里切换。 */
    val isCurrent: Boolean,
)

@Dao
interface PlanDao {
    @Upsert
    suspend fun upsert(plan: PlanEntity)

    @Query("UPDATE plan SET isCurrent = 0 WHERE planId = :planId AND isCurrent = 1")
    suspend fun clearCurrent(planId: String)

    @Transaction
    suspend fun saveCurrent(plan: PlanEntity, expectedVersion: Int?): Boolean {
        val current = currentByPlanId(plan.planId)?.version
        if (current != expectedVersion || (current != null && plan.version < current)) return false
        clearCurrent(plan.planId)
        upsert(plan.copy(isCurrent = true))
        return true
    }

    @Query("SELECT * FROM plan WHERE planId = :planId AND isCurrent = 1 LIMIT 1")
    suspend fun currentByPlanId(planId: String): PlanEntity?

    /** 版本历史（含旧版本），审计"这次为什么这样提示"用（技术文档 2.2） */
    @Query("SELECT * FROM plan WHERE planId = :planId ORDER BY version DESC")
    suspend fun history(planId: String): List<PlanEntity>

    @Query("SELECT * FROM plan WHERE isCurrent = 1 AND status IN ('DEPLOYED','ACTIVE')")
    suspend fun deployedOrActive(): List<PlanEntity>

    @Query("SELECT * FROM plan WHERE isCurrent = 1 ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<PlanEntity>>
}

@Entity(tableName = "action_log")
data class ActionLogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val ts: Long,
    val planId: String,
    val batchId: String,
    val actionType: String,
    val tier: String,
    val payloadSummary: String,
    /** confirmed / done / failed / undone / rejected_unknown / rejected_permission / rejected_already_executed */
    val decision: String,
    /** 当时依据面板的内容（evidence_json，技术文档 12.2 第 5 步） */
    val evidenceJson: String,
    /** undo=true 的动作在这里存恢复数据（event_id 等） */
    val undoData: String,
)

@Dao
interface ActionLogDao {
    @Upsert
    suspend fun insert(log: ActionLogEntity)

    @Query("SELECT * FROM action_log WHERE batchId = :batchId ORDER BY ts")
    suspend fun byBatch(batchId: String): List<ActionLogEntity>

    /** 幂等检查：同 batch 同类型动作是否已经执行过（审计收紧-1：重复确认只产生一条系统日历事件） */
    @Query(
        "SELECT * FROM action_log WHERE batchId = :batchId AND actionType = :actionType " +
            "AND decision = 'done' LIMIT 1",
    )
    suspend fun findExecuted(batchId: String, actionType: String): ActionLogEntity?
}

@Entity(tableName = "alarm", primaryKeys = ["planId", "kind"])
data class AlarmEntity(
    val planId: String,
    /** depart（出门提醒）/ tick（监控唤醒） */
    val kind: String,
    val triggerAt: Long,
)

@Dao
interface AlarmDao {
    @Upsert
    suspend fun upsert(alarm: AlarmEntity)

    @Query("DELETE FROM alarm WHERE planId = :planId")
    suspend fun deleteByPlanId(planId: String)

    @Query(
        "DELETE FROM alarm WHERE planId = :planId AND kind = :kind " +
            "AND triggerAt = :triggerAt AND triggerAt <= :now",
    )
    suspend fun consume(planId: String, kind: String, triggerAt: Long, now: Long): Int

    @Query("SELECT * FROM alarm WHERE triggerAt > :now")
    suspend fun pending(now: Long): List<AlarmEntity>
}

@Database(
    entities = [PlanEntity::class, ActionLogEntity::class, AlarmEntity::class],
    version = 2,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun planDao(): PlanDao
    abstract fun actionLogDao(): ActionLogDao
    abstract fun alarmDao(): AlarmDao
}

val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE plan_v2 (
                planId TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
                json TEXT NOT NULL, updatedAt INTEGER NOT NULL, isCurrent INTEGER NOT NULL,
                PRIMARY KEY (planId, version)
            )
            """.trimIndent(),
        )
        db.execSQL(
            "INSERT INTO plan_v2 (planId, version, status, json, updatedAt, isCurrent) " +
                "SELECT planId, version, status, json, updatedAt, 1 FROM plan",
        )
        db.execSQL("DROP TABLE plan")
        db.execSQL("ALTER TABLE plan_v2 RENAME TO plan")
        db.execSQL(
            """
            CREATE TABLE action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, ts INTEGER NOT NULL,
                planId TEXT NOT NULL, batchId TEXT NOT NULL, actionType TEXT NOT NULL,
                tier TEXT NOT NULL, payloadSummary TEXT NOT NULL, decision TEXT NOT NULL,
                evidenceJson TEXT NOT NULL, undoData TEXT NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE alarm (
                planId TEXT NOT NULL, kind TEXT NOT NULL, triggerAt INTEGER NOT NULL,
                PRIMARY KEY (planId, kind)
            )
            """.trimIndent(),
        )
    }
}
