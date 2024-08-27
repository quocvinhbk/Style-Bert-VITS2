.DEFAULT_GOAL := help

# References:
# - Makefile self documenting
#   https://gist.github.com/prwhite/8168133
.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "; printf "\nUsage: \033[36m\033[0m\n"}; {printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'

# Usage:
#   make clean
clean: ## Clean up dangling Docker images (Ex: make clean)
	docker rmi -f `docker images --filter "dangling=true" -q --no-trunc`

# Usage:
#   make down
down: ## Stop and remove container (Ex: make down)
	docker-compose down

# Usage:
#   make build
build: ## Build image (Ex: make build)
	docker-compose build
	$(MAKE) --ignore-errors clean

# Usage:
#   make up
up: down ## Build and run our container (Ex: make up)
	docker-compose up --build --detach
	$(MAKE) --ignore-errors clean

# Usage:
#   make top
top: ## Display the running processes (Ex: make top)
	docker-compose top app

# Usage:
#   make images
images: ## List images (Ex: make images)
	docker-compose images

# Usage:
#   make ps
ps: ## List containers (Ex: make ps)
	docker-compose ps

# Usage:
#   make exec
exec: ## Execute command in a our container (Ex: make exec)
	docker-compose exec app bash

# Usage:
# - View container log
#     make logs
logs: ## View container log (Ex: make logs)
	docker-compose logs -f app

# Usage:
# - Stop our container
#     make stop
stop: ## Stop our container (Ex: make stop)
	docker-compose stop

# Usage:
# - Start our container
#     make start
start: ## Start our container (ex: make start)
	docker-compose start

# Usage:
# - Restart our container
#     make restart
restart: ## Restart our container (Ex: make restart)
	docker-compose restart
