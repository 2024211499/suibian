package com.suibian.app

import com.suibian.app.ui.home.HomePlan
import com.suibian.app.ui.home.HomeState
import com.suibian.app.ui.home.PlanInspection
import com.suibian.app.ui.home.RainComparison
import com.suibian.app.ui.home.parseDepartureMinute
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HomePlanTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `plan retains complete graph and server version when saved and inspected`() {
        val graph = objectFrom(
            """{
                "plan_id":"p_demo01","version":3,"status":"DRAFT","title":"明早赶课","date":"2026-12-05",
                "nodes":[{"id":"n0","name":"宿舍"},{"id":"n1","name":"三教","window":{"latest":"08:00"}}],
                "edges":[{"id":"e1","from":"n0","to":"n1","eta_min":15}],"constraints":[]
            }""",
        )
        val plan = HomePlan.from(graph, json)
        val saved = plan.toEntity(updatedAt = 10)
        assertEquals("p_demo01", saved.planId)
        assertEquals(3, saved.version)
        assertEquals("08:00", plan.summary.nodes.last().window?.latest)
        val reopened = HomePlan.from(objectFrom(saved.json), json)
        assertEquals(graph, reopened.inspectionRequest(420)["plan"])
        assertEquals("420", reopened.inspectionRequest(420)["depart_min"].toString())
        val rainRequest = requireNotNull(reopened.rainRequest(420, 15))
        assertEquals(graph, rainRequest["plan"])
        assertEquals("420", rainRequest["depart_min"].toString())
        assertEquals(
            objectFrom("""{"kind":"route_delay","data":{"edge_id":"e1","delay_min":15}}"""),
            rainRequest["trigger"],
        )
    }

    @Test
    fun `zero probability remains distinct from unavailable latest departure`() {
        val inspection = PlanInspection.from(
            objectFrom("""{"sim":{"p_all":0.0,"depart_by":null},"evidence":{}}"""),
            json,
        )
        assertEquals(0.0, inspection.probability, 0.0)
        assertNull(inspection.departBy)
    }

    @Test
    fun `departure input accepts a 24 hour time and rejects malformed values`() {
        assertEquals(450, parseDepartureMinute("07:30"))
        assertEquals(450, parseDepartureMinute(" 7:30 "))
        assertEquals(0, parseDepartureMinute("00:00"))
        assertEquals(1439, parseDepartureMinute("23:59"))
        listOf("", "24:00", "12:60", "7:5", "07:30:00", "12pm").forEach {
            assertNull(parseDepartureMinute(it))
        }
    }

    @Test
    fun `rechecks use custom departure and delay on the same starting leg`() {
        val graph = objectFrom(
            """{
                "plan_id":"sample","version":4,"status":"DRAFT","title":"按时上课","date":"2026-12-05",
                "nodes":[{"id":"origin","name":"宿舍","kind":"origin"},{"id":"stop","name":"三教"}],
                "edges":[{"id":"second","from":"stop"},{"id":"first","from":"origin"}]
            }""",
        )
        val plan = HomePlan.from(graph, json)
        val request = requireNotNull(plan.rainRequest(450, 30))
        assertEquals(graph, request["plan"])
        assertEquals("450", request["depart_min"].toString())
        assertEquals(objectFrom("""{"kind":"route_delay","data":{"edge_id":"first","delay_min":30}}"""), request["trigger"])
        assertEquals("440", plan.rainRequest(440, 30)?.get("depart_min").toString())
    }

    @Test
    fun `changing departure invalidates both simulations but changing delay only invalidates rain`() {
        val inspection = PlanInspection(probability = 1.0, departBy = "07:43")
        val rain = RainComparison(1, inspection, inspection, emptyList(), emptyList())
        val checked = HomeState(inspection = inspection, rain = rain, saved = true)
        val later = checked.withDeparture(450)
        assertEquals(450, later.departureMinute)
        assertNull(later.inspection)
        assertNull(later.rain)
        assertEquals(true, later.saved)
        val delayed = checked.withRainDelay(30)
        assertEquals(30, delayed.rainDelayMinutes)
        assertEquals(inspection, delayed.inspection)
        assertNull(delayed.rain)
        val closed = delayed.withPlan(null)
        assertEquals(HomeState(), closed)
    }

    @Test
    fun `rain comparison retains arrival impact when no acceptable option exists`() {
        val response = objectFrom(
            """{
                "base_version":1,"before":{"p_all":1.0},"after":{"p_all":0.4},
                "affected":[{"node":"n1","before":{"arrive_p90":438.5},"after":{"arrive_p90":461.2}}],
                "options":[]
            }""",
        )
        val comparison = json.decodeFromJsonElement<RainComparison>(response)
        assertEquals(1, comparison.baseVersion)
        assertEquals(0.4, comparison.after.probability, 0.0)
        val impact = comparison.affected.single()
        assertEquals("n1", impact.node)
        assertEquals(438.5, impact.before.arrivalP90, 0.0)
        assertEquals(461.2, impact.after.arrivalP90, 0.0)
        assertEquals(emptyList<Any>(), comparison.options)
    }

    private fun objectFrom(value: String): JsonObject = json.parseToJsonElement(value).jsonObject
}
