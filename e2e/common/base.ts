// The single import surface for every spec: `test` (extended with the
// auth-seeded `page` + all page-object and step fixtures via DI) and `expect`.
// Specs import from here, NEVER from @playwright/test, and receive their step
// classes via DI — zero `new` in specs.
//
// The fixture definitions themselves live in `fixture.ts`; this file is the
// stable barrel specs depend on, so the `test.extend` wiring can change without
// touching a single spec's import line.
export { default, expect } from '@common/fixture';
