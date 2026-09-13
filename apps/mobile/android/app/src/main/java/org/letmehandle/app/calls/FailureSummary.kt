package org.letmehandle.app.calls

/** A failure named by its exception classes only, so nothing it quoted reaches the log. */
object FailureSummary {
  fun of(failure: Throwable): String =
      generateSequence(failure) { it.cause }.joinToString(" <- ") { it.javaClass.name }
}
