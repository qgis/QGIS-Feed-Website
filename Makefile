CONTAINER_NAME := qgisfeed
SHELL := /bin/bash

# ----------------------------------------------------------------------------
#    D E V E L O P M E N T    C O M M A N D S
# ----------------------------------------------------------------------------
default: dev-build
run: dev-build dev-start

dev-build:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Building in development mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml build

dev-start:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running in development mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml up -d

dev-logs:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Show the logs in development mode"
	@echo "------------------------------------------------------------------"
	@docker logs -f $(CONTAINER_NAME)

dev-updatemigrations:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running makemigrations in development mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py makemigrations

dev-migrate:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running migrate in development mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py migrate

dev-dbseed:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Seed db with JSON data from /fixtures/*.json"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py loaddata qgisfeedproject/qgisfeed/fixtures/*.json

dev-createsuperuser:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Create an admin user"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py createsuperuser

dev-runtests:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running tests in development mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py test qgisfeed

dev-stop:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Stopping the development server"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose.dev.yml down

# ----------------------------------------------------------------------------
#    P R O D U C T I O N    C O M M A N D S
# ----------------------------------------------------------------------------

build:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Building in production mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml build

start:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running in production mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml up -d

updatemigrations:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running in production mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py makemigrations

migrate:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Running migrate in production mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py migrate

createsuperuser:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Create an admin user"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py createsuperuser

collectstatic:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Collecting static in production mode"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml exec $(CONTAINER_NAME) python qgisfeedproject/manage.py collectstatic --noinput

stop:
	@echo
	@echo "------------------------------------------------------------------"
	@echo "Stop the production server"
	@echo "------------------------------------------------------------------"
	@docker-compose -f docker-compose-production-ssl.yml down
