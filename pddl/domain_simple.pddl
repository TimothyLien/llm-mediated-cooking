(define (domain fetch-domain)
  (:requirements :strips :typing)

  (:types
    agent room item
  )

  (:predicates
    (at ?a - agent ?r - room)
    (connected ?r1 - room ?r2 - room)
    (in-room ?i - item ?r - room)
    (holding ?a - agent ?i - item)
    (empty-hand ?a - agent)

    ;; --- HRC Constraints ---
    (can-enter ?a - agent ?r - room)
    (can-take ?a - agent ?i - item)
  )

  ;; -------------------------
  ;; MOVE
  ;; -------------------------
  (:action move
    :parameters (?a - agent ?from - room ?to - room)
    :precondition (and
      (at ?a ?from)
      (connected ?from ?to)
      (can-enter ?a ?to)
    )
    :effect (and
      (not (at ?a ?from))
      (at ?a ?to)
    )
  )

  ;; -------------------------
  ;; TAKE
  ;; -------------------------
  (:action take
    :parameters (?a - agent ?i - item ?r - room)
    :precondition (and
      (at ?a ?r)
      (in-room ?i ?r)
      (empty-hand ?a)
      (can-take ?a ?i)
    )
    :effect (and
      (not (in-room ?i ?r))
      (not (empty-hand ?a))
      (holding ?a ?i)
    )
  )

  ;; -------------------------
  ;; DROP
  ;; -------------------------
  (:action drop
    :parameters (?a - agent ?i - item ?r - room)
    :precondition (and
      (at ?a ?r)
      (holding ?a ?i)
    )
    :effect (and
      (not (holding ?a ?i))
      (in-room ?i ?r)
      (empty-hand ?a)
    )
  )
)
