# Forking EPUBCheck to emit native `KOBO-*` warnings

This is the **optional** path: a source fork of EPUBCheck that reports the
Kobo/Adobe-RMSDK CSS problems as first-class messages **inside EPUBCheck's own
report stream**, tunable with `--customMessages`. Most users want
`kobofix --check` instead (no toolchain, wraps the stock validator). Build this
only if a pipeline specifically needs the warnings emitted by EPUBCheck itself.

> Verified against the EPUBCheck source. The clone used for line numbers was
> `5.3.1-SNAPSHOT`; **fork the `v4.2.6` tag** if you need the jar to run on a
> Java 8 JRE (5.x requires Java 11+). 4.2.6 is also the version Kobo runs, so
> it's the most faithful target. The class/method *names* below are stable
> across versions; re-confirm line numbers on your chosen tag.

## Prerequisites (not present in the original build environment)

- A JDK (Temurin/Adoptium **JDK 8** matches `pom.xml` `java.version=1.8`).
- Apache **Maven** ≥ 3.0 (the repo has no `mvnw` wrapper; a system `mvn` is required).

```
git clone --branch v4.2.6 --depth 1 https://github.com/w3c/epubcheck.git
cd epubcheck
```

## The four edits

All CSS — standalone `.css`, inline `<style>`, and `style=""` — routes through the
**same** `com.adobe.epubcheck.css.CSSHandler`, so one hook covers every case.

### 1. `src/main/java/com/adobe/epubcheck/messages/MessageId.java`
Add three constants in the CSS block (comma-terminated, before the final
`SCP_010("SCP-010");`):
```java
KOBO_001("KOBO-001"),
KOBO_002("KOBO-002"),
KOBO_003("KOBO-003"),
```

### 2. `src/main/java/com/adobe/epubcheck/messages/DefaultSeverities.java`
**Required** — a missing entry throws `IllegalArgumentException` at first emit.
In `initialize()`, near the other CSS ids:
```java
severities.put(MessageId.KOBO_001, Severity.WARNING);
severities.put(MessageId.KOBO_002, Severity.WARNING);
severities.put(MessageId.KOBO_003, Severity.USAGE);
```

### 3. `src/main/resources/com/adobe/epubcheck/messages/MessageBundle.properties`
Keys use the **underscore** form. (Locale bundles fall back to this base file.)
```properties
KOBO_001=The CSS function "%1$s" is unsupported by legacy Adobe RMSDK (Kobo/ADE) and can cause the entire stylesheet to be dropped or the book to fail to open.
KOBO_001_SUG=Remove it or replace it with a static value (kobofix can do this automatically).
KOBO_002=The viewport unit in the "%1$s" property is unreliable on legacy RMSDK; in a margin it can crash Kobo to a blank screen.
KOBO_003=Empty @media/@supports block; some legacy RMSDK builds crash on these.
```

### 4. `src/main/java/com/adobe/epubcheck/css/CSSHandler.java`
Add `import com.google.common.collect.ImmutableSet;` (Guava is already on the
classpath — `com.adobe...Sets` is imported nearby) and a constant:
```java
private static final ImmutableSet<String> KOBO_FNS =
    ImmutableSet.of("calc", "min", "max", "clamp", "var", "env");
```

**(A) Functions + (B) viewport-in-margin** — inside `declaration(CssDeclaration declaration)`,
after the existing property checks:
```java
String prop = declaration.getName().get();                 // lowercased
boolean marginish = prop.equals("margin") || prop.startsWith("margin-");
for (CssConstruct comp : declaration.getComponents()) {
    for (CssConstruct c : CssGrammar.flatten(comp)) {       // recurses into calc(var(...))
        if (c.getType() == CssConstruct.Type.FUNCTION) {
            String fn = ((CssGrammar.CssFunction) c).getName().get();
            if (KOBO_FNS.contains(fn)) {
                report.message(MessageId.KOBO_001,
                    getCorrectedEPUBLocation(declaration), fn + "()");
            }
        } else if (marginish && c.getType() == CssConstruct.Type.QUANTITY) {
            // vw/vh/vmin/vmax all tokenize to Unit.LENGTH; inspect the raw suffix.
            String q = c.toCssString().toLowerCase();
            if (q.endsWith("vmin") || q.endsWith("vmax")
                    || q.endsWith("vw") || q.endsWith("vh")) {
                report.message(MessageId.KOBO_002,
                    getCorrectedEPUBLocation(declaration), prop);
            }
        }
    }
}
```

**(C) Empty `@media`** — cannot be seen in `declaration()`; mirror the existing
`inFontFace → CSS_019` (empty `@font-face`) pattern. Add fields, then:
- `startAtRule(CssAtRule atRule)`: if `atRule.getName().get().equals("@media")`,
  set `inMedia = true; mediaChildCount = 0;` and **capture**
  `mediaLoc = getCorrectedEPUBLocation(atRule);` (`endAtRule` nulls `atRule`, so
  don't dereference it later).
- `selectors(...)` and `declaration(...)`: `if (inMedia) mediaChildCount++;`
- `endAtRule(String name)`: `if (inMedia) { inMedia = false;
  if (mediaChildCount == 0) report.message(MessageId.KOBO_003, mediaLoc); }`

## Build & run

```
mvn -DskipTests clean package        # iterate
java -jar target/epubcheck.jar book.epub
```
`package` produces `target/epubcheck.jar`, `target/lib/`, and the distributable
`target/epubcheck.zip`. For CI, add a Cucumber `.feature` fixture asserting the
new ids, then run full `mvn clean install` (the suite asserts on reported ids,
so new output without a fixture can fail unrelated tests).

## Constraints to respect

- **Rename the distributable.** BSD-3-Clause clause 3 forbids shipping a fork
  under the EPUBCheck / W3C / IDPF / Adobe name in a way implying endorsement.
  Keep `LICENSE.md` and `src/main/licenses/` notices.
- **Maintenance:** re-apply on every upstream release; the hook is anchored on
  stable method names but line numbers drift.
- **Noise:** `var()`/`calc()` are common in modern EPUB3; ship the ids at
  `WARNING` and let pipelines silence/raise them via `--customMessages`.
