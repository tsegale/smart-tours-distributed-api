package com.smarttours.gateway.ratelimit;

import org.springframework.web.server.ServerWebExchange;

import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.UnknownHostException;
import java.util.Arrays;
import java.util.List;

/**
 * The client's IP for rate limiting. X-Forwarded-For is trusted only when the connection comes from a
 * configured proxy (a load balancer in front of the gateway). Otherwise anyone could send a fresh
 * random X-Forwarded-For with every request and never run out of tokens.
 *
 * <p>With trusted proxies, the header is read right to left and the first address that is not itself a
 * trusted proxy is the client: entries to the left of it were written by the client and prove nothing.
 */
public class ClientAddressResolver {

    static final String UNKNOWN = "unknown";

    private final List<Cidr> trustedProxies;

    public ClientAddressResolver(List<String> trustedProxyCidrs) {
        this.trustedProxies = trustedProxyCidrs.stream()
                .map(String::trim)
                .filter(cidr -> !cidr.isEmpty())
                .map(Cidr::parse)
                .toList();
    }

    public String resolve(ServerWebExchange exchange) {
        InetSocketAddress remote = exchange.getRequest().getRemoteAddress();
        if (remote == null || remote.getAddress() == null) {
            return UNKNOWN;
        }
        String peer = remote.getAddress().getHostAddress();
        if (!isTrusted(peer)) {
            return peer;
        }
        List<String> forwarded = exchange.getRequest().getHeaders().getValuesAsList("X-Forwarded-For").stream()
                .flatMap(value -> Arrays.stream(value.split(",")))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .toList();
        for (int i = forwarded.size() - 1; i >= 0; i--) {
            String hop = forwarded.get(i);
            if (!isTrusted(hop)) {
                return hop;
            }
        }
        return peer; // every hop is one of our proxies: the request started inside
    }

    private boolean isTrusted(String address) {
        InetAddress parsed = parseLiteral(address);
        return parsed != null && trustedProxies.stream().anyMatch(cidr -> cidr.contains(parsed));
    }

    /**
     * An IP literal, or null for anything else. Never resolves names: a forged header must not make the
     * gateway do DNS lookups.
     */
    static InetAddress parseLiteral(String value) {
        if (value.isEmpty() || !value.matches("[0-9a-fA-F:.]+")) {
            return null;
        }
        try {
            return InetAddress.getByName(value);
        } catch (UnknownHostException ex) {
            return null;
        }
    }

    /** An address block such as 10.0.0.0/8, or a single address. */
    record Cidr(byte[] network, int prefixLength) {

        static Cidr parse(String cidr) {
            String[] parts = cidr.split("/", 2);
            InetAddress address = parseLiteral(parts[0]);
            if (address == null) {
                throw new IllegalArgumentException("not an IP address or CIDR block: " + cidr);
            }
            int bits = address.getAddress().length * 8;
            int prefix = parts.length == 2 ? Integer.parseInt(parts[1]) : bits;
            if (prefix < 0 || prefix > bits) {
                throw new IllegalArgumentException("bad prefix length in " + cidr);
            }
            return new Cidr(address.getAddress(), prefix);
        }

        boolean contains(InetAddress address) {
            byte[] candidate = address.getAddress();
            if (candidate.length != network.length) {
                return false;
            }
            int fullBytes = prefixLength / 8;
            for (int i = 0; i < fullBytes; i++) {
                if (candidate[i] != network[i]) {
                    return false;
                }
            }
            int remainingBits = prefixLength % 8;
            if (remainingBits == 0) {
                return true;
            }
            int mask = (0xFF << (8 - remainingBits)) & 0xFF;
            return (candidate[fullBytes] & mask) == (network[fullBytes] & mask);
        }
    }
}
