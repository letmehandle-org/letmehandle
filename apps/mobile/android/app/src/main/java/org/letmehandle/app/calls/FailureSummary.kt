package org.letmehandle.app.calls

/**
 * A failure on the screening path, described for the log without anything it was holding.
 *
 * The messages there are not safe to log: org.json quotes the text it could not read, and that
 * text is the stored rules or the unreported calls, which hold callers' and contacts' numbers.
 * The kinds of failure, outermost first, are enough to tell one fault from another.
 */
object FailureSummary {
  fun of(failure: Throwable): String =
      generateSequence(failure) { it.cause }.joinToString(" <- ") { it.javaClass.name }
}
