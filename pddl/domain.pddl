(define (domain sandwich-domain)
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

    (fixed ?i - item)

    (is-kitchen ?r)

    (is-ham ?i)
    (is-lettuce ?i)

    (is-sliced ?i)
    (is-washed ?i)

    (is-bread ?i)
    (is-cheese ?i)

    (knife-board-present ?r - room)
    (sink-present ?r - room)

    ;; --- HRC Constraints ---
    (can-enter ?a - agent ?r - room)
    (can-take ?a - agent ?i - item)
    (can-slice ?a - agent)
    (can-wash ?a - agent)
    (can-assemble ?a - agent)

    ;; --- GOAL ---
    (sandwich-made)
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
      (not (fixed ?i))
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

  (:action slice-ham
    :parameters (?a - agent ?r - room ?i - item)
    :precondition (and
      (at ?a ?r)
      (holding ?a ?i)
      (is-ham ?i)
      (knife-board-present ?r)
      (can-slice ?a)
    )
    :effect (and
      (is-sliced ?i)
    )
  )


  (:action wash-lettuce
    :parameters (?a - agent ?r - room ?i - item)
    :precondition (and
      (at ?a ?r)
      (holding ?a ?i)
      (is-lettuce ?i)
      (sink-present ?r)
      (can-wash ?a)
    )
    :effect (and
      (is-washed ?i)
    )
  )

  ;; make sandwich: require distinct ingredient objects and require kitchen
  (:action make-sandwich
    :parameters (?a - agent ?r - room
                 ?bread - item ?cheese - item ?ham - item ?lettuce - item)
    :precondition (and
      (is-kitchen ?r)
      (at ?a ?r)
      (in-room ?bread ?r)
      (in-room ?cheese ?r)
      (in-room ?ham ?r)
      (in-room ?lettuce ?r)

      (is-bread ?bread)
      (is-cheese ?cheese)
      (is-ham ?ham)
      (is-lettuce ?lettuce)

      (is-sliced ?ham)
      (is-washed ?lettuce)

      ;; enforce distinct objects (no reusing same object twice)
      (not (= ?bread ?cheese))
      (not (= ?bread ?ham))
      (not (= ?bread ?lettuce))
      (not (= ?cheese ?ham))
      (not (= ?cheese ?lettuce))
      (not (= ?ham ?lettuce))

      (empty-hand ?a)
      (can-assemble ?a)
    )
    :effect (and
      (not (in-room ?bread ?r)) (not (in-room ?cheese ?r))
      (not (in-room ?ham ?r)) (not (in-room ?lettuce ?r))
      (sandwich-made)
    )
  )
)
