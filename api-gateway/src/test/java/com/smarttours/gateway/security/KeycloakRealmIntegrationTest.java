package com.smarttours.gateway.security;

import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.reactive.server.SecurityMockServerConfigurers.springSecurity;

/**
 * The repo's realm file imported into a real Keycloak, the version the auth profile runs, and real tokens through the
 * gateway's real security chain. Users log in the way a browser does: the public client's code flow with PKCE.
 * Downstream services aren't running, so passing security ends in a 5xx.
 */
@Testcontainers
@SpringBootTest(properties = {
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class KeycloakRealmIntegrationTest {

    private static final String PASSWORD = "integration-test-password";
    private static final String TOOLING_SECRET = "integration-test-tooling-secret";
    private static final String WEB_ORIGIN = "http://localhost:3000";
    private static final String REDIRECT_URI = WEB_ORIGIN + "/callback";
    private static final Pattern LOGIN_FORM = Pattern.compile("<form[^>]*action=\"([^\"]+)\"");

    @Container
    private static final GenericContainer<?> KEYCLOAK = new GenericContainer<>(DockerImageName.parse(keycloakImage()))
            .withCommand("start-dev", "--import-realm", "--health-enabled=true")
            .withCopyFileToContainer(
                    MountableFile.forHostPath(Path.of("../infra/keycloak/realm/smart-tours-realm.json")),
                    "/opt/keycloak/data/import/smart-tours-realm.json")
            .withEnv("KEYCLOAK_WEB_ORIGIN", WEB_ORIGIN)
            .withEnv("KEYCLOAK_OPERATOR_TOOLING_SECRET", TOOLING_SECRET)
            .withEnv("KEYCLOAK_DEMO_CUSTOMER_PASSWORD", PASSWORD)
            .withEnv("KEYCLOAK_DEMO_OPERATOR_PASSWORD", PASSWORD)
            .withEnv("KEYCLOAK_DEMO_ADMIN_PASSWORD", PASSWORD)
            .withExposedPorts(8080, 9000)
            .waitingFor(Wait.forHttp("/health/ready").forPort(9000).withStartupTimeout(Duration.ofMinutes(4)));

    private final HttpClient http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();

    @Autowired
    private ApplicationContext context;

    private WebTestClient client;

    @DynamicPropertySource
    static void keycloak(DynamicPropertyRegistry registry) {
        registry.add("smarttours.security.keycloak.issuer-uri", KeycloakRealmIntegrationTest::issuer);
    }

    @BeforeEach
    void setUp() {
        client = WebTestClient.bindToApplicationContext(context).apply(springSecurity()).configureClient().build();
    }

    @Test
    @Order(1)
    void eachDemoUserGetsExactlyTheirRole_aTopLevelClaimAnd_anRs256Token() throws Exception {
        for (var user : Map.of(
                "demo-customer", "CUSTOMER", "demo-operator", "OPERATOR", "demo-admin", "ADMIN").entrySet()) {
            SignedJWT jwt = SignedJWT.parse(login(user.getKey(), PASSWORD));

            assertThat(jwt.getHeader().getAlgorithm().getName()).isEqualTo("RS256");
            assertThat(jwt.getJWTClaimsSet().getIssuer()).isEqualTo(issuer());
            assertThat(jwt.getJWTClaimsSet().getAudience()).contains("smart-tours-api");
            assertThat(jwt.getJWTClaimsSet().getSubject()).isNotBlank();
            // Exactly: Keycloak's built-in default-roles-* and offline_access must not leak into X-User-Roles.
            assertThat(jwt.getJWTClaimsSet().getStringListClaim("roles")).containsExactly(user.getValue());
            assertThat(jwt.getJWTClaimsSet().getStringClaim("email")).isEqualTo(user.getKey() + "@example.com");
        }
    }

    @Test
    @Order(2)
    void accessTokensLastFiveMinutes() throws Exception {
        var claims = SignedJWT.parse(login("demo-customer", PASSWORD)).getJWTClaimsSet();
        assertThat(claims.getExpirationTime().getTime() - claims.getIssueTime().getTime()).isEqualTo(300_000);
    }

    @Test
    @Order(3)
    void theGatewayEnforcesKeycloakRoles() throws Exception {
        String customer = login("demo-customer", PASSWORD);
        String operator = login("demo-operator", PASSWORD);
        String admin = login("demo-admin", PASSWORD);

        assertPassesSecurity(client.get().uri("/api/bookings/me"), customer);
        assertStatus(client.post().uri("/api/tours"), customer, 403);
        assertPassesSecurity(client.post().uri("/api/tours"), operator);
        assertStatus(client.post().uri("/api/ai/embed"), operator, 403);
        assertPassesSecurity(client.post().uri("/api/ai/embed"), admin);
    }

    @Test
    @Order(4)
    void theToolingClientActsAsAnOperator() throws Exception {
        HttpResponse<String> response = token(Map.of(
                "grant_type", "client_credentials",
                "client_id", "smart-tours-operator-tooling",
                "client_secret", TOOLING_SECRET));
        assertThat(response.statusCode()).isEqualTo(200);
        String token = accessToken(response.body());

        assertThat(SignedJWT.parse(token).getJWTClaimsSet().getStringListClaim("roles")).containsExactly("OPERATOR");
        assertPassesSecurity(client.post().uri("/api/tours"), token);
        assertStatus(client.post().uri("/api/ai/embed"), token, 403);
    }

    @Test
    @Order(5)
    void theToolingClientNeedsItsSecret() throws Exception {
        assertThat(token(Map.of(
                "grant_type", "client_credentials",
                "client_id", "smart-tours-operator-tooling",
                "client_secret", "wrong")).statusCode()).isEqualTo(401);
    }

    @Test
    @Order(6)
    void thePublicClientHasNoPasswordGrant() throws Exception {
        HttpResponse<String> response = token(Map.of(
                "grant_type", "password",
                "client_id", "smart-tours-web",
                "username", "demo-customer",
                "password", PASSWORD));
        assertThat(response.statusCode()).isIn(400, 401);
        assertThat(response.body()).doesNotContain("access_token");
    }

    @Test
    @Order(7)
    void thePublicClientRefusesALoginWithoutPkce() throws Exception {
        String url = issuer() + "/protocol/openid-connect/auth?" + form(Map.of(
                "client_id", "smart-tours-web",
                "response_type", "code",
                "scope", "openid",
                "redirect_uri", REDIRECT_URI));
        HttpResponse<String> response = http.send(
                HttpRequest.newBuilder(URI.create(url)).GET().build(), HttpResponse.BodyHandlers.ofString());

        assertThat(LOGIN_FORM.matcher(response.body()).find()).isFalse();
        assertThat(response.headers().firstValue("Location").orElse("")).contains("error=invalid_request");
    }

    @Test
    @Order(8)
    void thePublicClientRefusesAForeignRedirect() throws Exception {
        String url = issuer() + "/protocol/openid-connect/auth?" + form(Map.of(
                "client_id", "smart-tours-web",
                "response_type", "code",
                "scope", "openid",
                "redirect_uri", "https://evil.example.test/callback",
                "code_challenge", challenge("a-verifier"),
                "code_challenge_method", "S256"));
        HttpResponse<String> response = http.send(
                HttpRequest.newBuilder(URI.create(url)).GET().build(), HttpResponse.BodyHandlers.ofString());

        assertThat(response.statusCode()).isEqualTo(400);
    }

    /** Last, because it locks the account for a while. */
    @Test
    @Order(100)
    void repeatedFailedLoginsLockTheAccount() throws Exception {
        for (int attempt = 0; attempt < 5; attempt++) {
            assertThat(tryLogin("demo-customer", "wrong-password").statusCode()).isEqualTo(200);
        }
        HttpResponse<String> correctPassword = tryLogin("demo-customer", PASSWORD);

        assertThat(correctPassword.statusCode()).isEqualTo(200); // the form again, not a redirect with a code
        assertThat(correctPassword.headers().firstValue("Location")).isEmpty();
    }

    private String login(String username, String password) throws Exception {
        String verifier = Base64.getUrlEncoder().withoutPadding().encodeToString(("verifier-" + System.nanoTime()
                + "-0123456789-0123456789-0123456789").getBytes(StandardCharsets.UTF_8));
        HttpResponse<String> redirect = submitLogin(username, password, challenge(verifier));
        assertThat(redirect.statusCode()).as("login for %s", username).isEqualTo(302);

        String code = query(URI.create(redirect.headers().firstValue("Location").orElseThrow())).get("code");
        HttpResponse<String> response = token(Map.of(
                "grant_type", "authorization_code",
                "client_id", "smart-tours-web",
                "code", code,
                "redirect_uri", REDIRECT_URI,
                "code_verifier", verifier));
        assertThat(response.statusCode()).isEqualTo(200);
        return accessToken(response.body());
    }

    private HttpResponse<String> tryLogin(String username, String password) throws Exception {
        return submitLogin(username, password, challenge("a-verifier-for-a-login-that-goes-no-further"));
    }

    private HttpResponse<String> submitLogin(String username, String password, String challenge) throws Exception {
        String url = issuer() + "/protocol/openid-connect/auth?" + form(Map.of(
                "client_id", "smart-tours-web",
                "response_type", "code",
                "scope", "openid",
                "redirect_uri", REDIRECT_URI,
                "state", "state",
                "code_challenge", challenge,
                "code_challenge_method", "S256"));
        HttpResponse<String> loginPage = http.send(HttpRequest.newBuilder(URI.create(url)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        Matcher action = LOGIN_FORM.matcher(loginPage.body());
        assertThat(action.find()).as("a login form in: %s", loginPage.body()).isTrue();
        // Keycloak marks these Secure. Browsers send them to localhost over plain HTTP, Java's CookieManager doesn't,
        // so they are passed on by hand. Each login starts without cookies: an earlier session would skip the form.
        String cookies = loginPage.headers().allValues("Set-Cookie").stream()
                .map(cookie -> cookie.split(";", 2)[0])
                .collect(Collectors.joining("; "));
        return http.send(
                HttpRequest.newBuilder(URI.create(action.group(1).replace("&amp;", "&")))
                        .header("Cookie", cookies)
                        .header("Content-Type", "application/x-www-form-urlencoded")
                        .POST(HttpRequest.BodyPublishers.ofString(form(Map.of(
                                "username", username, "password", password, "credentialId", ""))))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> token(Map<String, String> fields) throws IOException, InterruptedException {
        return http.send(
                HttpRequest.newBuilder(URI.create(issuer() + "/protocol/openid-connect/token"))
                        .header("Content-Type", "application/x-www-form-urlencoded")
                        .POST(HttpRequest.BodyPublishers.ofString(form(fields)))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
    }

    private void assertPassesSecurity(WebTestClient.RequestHeadersSpec<?> request, String token) {
        request.header("Authorization", "Bearer " + token).exchange()
                .expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    private void assertStatus(WebTestClient.RequestHeadersSpec<?> request, String token, int status) {
        request.header("Authorization", "Bearer " + token).exchange().expectStatus().isEqualTo(status);
    }

    private static String issuer() {
        return "http://%s:%d/realms/smart-tours".formatted(KEYCLOAK.getHost(), KEYCLOAK.getMappedPort(8080));
    }

    /** The same version the auth profile builds on, read from its Dockerfile so that the two can't drift apart. */
    private static String keycloakImage() {
        try {
            List<String> lines = Files.readAllLines(Path.of("../infra/keycloak/Dockerfile"));
            return lines.stream().filter(line -> line.startsWith("FROM ")).findFirst().orElseThrow().split(" ")[1];
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String accessToken(String body) {
        Matcher matcher = Pattern.compile("\"access_token\":\"([^\"]+)\"").matcher(body);
        assertThat(matcher.find()).isTrue();
        return matcher.group(1);
    }

    private static String challenge(String verifier) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.US_ASCII));
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
    }

    private static String form(Map<String, String> fields) {
        return fields.entrySet().stream()
                .map(field -> field.getKey() + "=" + URLEncoder.encode(field.getValue(), StandardCharsets.UTF_8))
                .collect(Collectors.joining("&"));
    }

    private static Map<String, String> query(URI uri) {
        return Arrays.stream(uri.getRawQuery().split("&"))
                .map(pair -> pair.split("=", 2))
                .collect(Collectors.toMap(pair -> pair[0], pair -> URLDecoder.decode(pair[1], StandardCharsets.UTF_8)));
    }
}
