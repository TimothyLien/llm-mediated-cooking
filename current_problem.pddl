(define (problem sandwich-problem)
  (:domain sandwich-domain)

  (:objects
    p1 p2 - agent
    kitchen pantry - room

    bread cheese ham lettuce
    sink knife-board - item
  )

  (:init
    ;; --- Player Locations ---
    (at p1 kitchen)
    (at p2 kitchen)

    (empty-hand p1)
    (empty-hand p2)

    ;; --- Room Connections ---
    (connected kitchen pantry)
    (connected pantry kitchen)

    ;; --- Item Locations ---
    (in-room cheese kitchen)
    (in-room lettuce kitchen)
    (in-room sink kitchen)
    (in-room knife-board kitchen)

    (in-room bread pantry)
    (in-room ham pantry)

    ;; --- Fixed Objects ---
    (fixed sink)
    (fixed knife-board)

    ;; --- Tool Presence Flags ---
    (sink-present kitchen)
    (knife-board-present kitchen)

    ;; --- Item Types ---
    (is-bread bread)
    (is-cheese cheese)
    (is-ham ham)
    (is-lettuce lettuce)

    ;; --- Room Types ---
    (is-kitchen kitchen)


    ;; --- HRC CONSTRAINT INITIALIZATIONS ---

    ;; Room Permissions
    (can-enter p1 kitchen)
    (can-enter p1 pantry)
    (can-enter p2 kitchen)
    (can-enter p2 pantry)

    ;; Ability to take items
    ;; (can-take p1 bread) -- REMOVED: human is allergic to bread
    (can-take p1 cheese)
    (can-take p1 ham)
    (can-take p1 lettuce)

    (can-take p2 bread)
    (can-take p2 cheese)
    (can-take p2 ham)
    (can-take p2 lettuce)

    ;; Ability to slice with knife
    (can-slice p1)
    ;; (can-slice p2) -- REMOVED: robot can't slice ham

    ;; Ability to wash
    (can-wash p1)
    ;; (can-wash p2) -- REMOVED: robot can't wash lettuce

    ;; Ability to assemble the sandwich
    (can-assemble p1)
    (can-assemble p2)
  )

  (:goal
    (sandwich-made)
  )
)