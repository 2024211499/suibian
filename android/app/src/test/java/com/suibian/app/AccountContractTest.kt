package com.suibian.app

import com.suibian.app.data.api.AccountCredentials
import com.suibian.app.data.api.AccountResponse
import com.suibian.app.data.api.accountRequest
import com.suibian.app.data.api.isProtectedApi
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AccountContractTest {
    private val api = "https://api.example.test/".toHttpUrl()
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun responseAndCredentialsUseTheServerContract() {
        val session = json.decodeFromString<AccountResponse>(
            """{"access_token":"test-token","token_type":"bearer","expires_in":86400,
                "user":{"id":"1","username":"demo","role":"user"}}""",
        ).session(now = 1_000L)
        assertEquals("test-token", session.token)
        assertEquals("demo", session.username)
        assertTrue(session.activeAt(86_400_999L))
        assertFalse(session.activeAt(86_401_000L))
        assertEquals("""{"username":"demo","password":"fixture-password"}""",
            json.encodeToString(AccountCredentials("demo", "fixture-password")))
    }

    @Test
    fun tokenGoesOnlyToProtectedEndpointsOnTheConfiguredOrigin() {
        for (path in listOf("v1/parse", "v1/simulate", "v1/replan", "v1/auth/me", "v1/auth/logout")) {
            val request = Request.Builder().url(checkNotNull(api.resolve(path))).build()
            assertTrue(isProtectedApi(request.url, api))
            assertEquals("Bearer test-token", accountRequest(request, api, "test-token").header("Authorization"))
        }
        for (path in listOf("v1/health", "v1/version", "v1/auth/register", "v1/auth/login",
            "https://other.example.test/v1/parse", "http://api.example.test/v1/parse")) {
            val request = Request.Builder().url(checkNotNull(api.resolve(path))).build()
            assertNull(accountRequest(request, api, "test-token").header("Authorization"))
        }
    }
}
