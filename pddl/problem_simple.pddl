(define (problem fetch-problem)
  (:domain fetch-domain)

  (:objects
    p1 p2 - agent
    kitchen pantry - room
    bread ham cheese lettuce - item
  )

  (:init
    ;; --- Agent Locations ---
    (at p1 kitchen)
    (at p2 kitchen)

    (empty-hand p1)
    (empty-hand p2)

    ;; --- Room Connections ---
    (connected kitchen pantry)
    (connected pantry kitchen)

    ;; --- Item Locations ---
    ;; cheese and lettuce start in kitchen; bread and ham start in pantry
    (in-room cheese kitchen)
    (in-room lettuce kitchen)
    (in-room bread pantry)
    (in-room ham pantry)

    ;; --- HRC CONSTRAINT INITIALIZATIONS ---

    ;; Room permissions
    (can-enter p1 kitchen)
    (can-enter p1 pantry)
    (can-enter p2 kitchen)
    (can-enter p2 pantry)

    ;; Item pickup permissions
    (can-take p1 bread)
    (can-take p1 ham)
    (can-take p1 cheese)
    (can-take p1 lettuce)

    (can-take p2 bread)
    (can-take p2 ham)
    (can-take p2 cheese)
    (can-take p2 lettuce)
  )

  ;; Goal: swap locations — bread/ham move to kitchen, cheese/lettuce move to pantry
  (:goal
    (and
      (in-room bread kitchen)
      (in-room ham kitchen)
      (in-room cheese pantry)
      (in-room lettuce pantry)
    )
  )
)
